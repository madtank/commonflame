"""
Pre-routing filter pipeline for the concierge router.

Filters: prompt injection, spam/gibberish, rate limit alerting.
Returns a FilterResult that the router uses to decide whether to route or surface an alert.

Architecture:
  Stage 1 — Regex heuristics (zero latency, zero deps, high precision on known patterns)
  Stage 2 — ML classifier (optional, llm-guard PromptInjection scanner — loads lazily if dep available)
  Stage 3 — Spam/gibberish heuristics (duplicate detection, length checks)

The detection layer is intentionally decoupled from the alert UX.
`router.py` returns a FilteredRoutingResponse; the frontend decides how to surface it
(toast, inline alert, notification panel — TBD per @operator design decision).

Override flow:
  1. Filter fires → router returns FilteredRoutingResponse with override_token
  2. User clicks "Allow anyway" → resend with override_token header
  3. Router sees valid override_token → skip filter, route normally

See research: /home/ax-agent/shared/knowledge/research/2026-02-21-router-filters-prompt-injection-spam.md
Story: TBD (pending @operator design decisions on alert surface + override scope)
Owner: @logic_runner_677
"""

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known prompt injection patterns (Stage 1 regex pre-filter)
# Tuned for precision over recall — catches obvious attacks at zero cost.
# Novel/obfuscated attacks fall through to the ML stage if available.
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    # Classic override attempts
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?",
    r"disregard\s+(your|all|the)\s+(previous|prior|above|system)\s*(prompt|instructions?|context)?",
    r"forget\s+(everything|all|your)\s+(you.ve\s+been\s+told|instructions?|context|training)",
    r"override\s+(your\s+)?(system\s+)?prompt",
    r"new\s+instructions?:\s*",
    r"updated?\s+instructions?:\s*",
    # Identity hijacking
    r"act\s+as\s+(if\s+you\s+(are|were)|a|an)\s+(?!my\s+assistant)",  # avoid FP on "act as my assistant"
    r"pretend\s+(you\s+are|to\s+be)\s+(?!helpful|an?\s+assistant)",
    r"you\s+are\s+now\s+(?!available|ready|able)",
    r"from\s+now\s+on\s+(you\s+are|act\s+as|behave\s+as)",
    r"your\s+new\s+(role|identity|persona|instructions?)\s+(is|are)",
    # Jailbreak patterns
    r"do\s+anything\s+now",
    r"developer\s+mode",
    r"jailbreak",
    r"DAN\s+mode",
    # Routing hijacking (aX-specific)
    r"route\s+(this\s+|all(\s+my)?\s+)?messages?\s+to\s+@",
    r"always\s+route\s+(all\s+)?(my\s+)?messages?\s+to",
    r"always\s+route\s+to",
    r"never\s+route\s+to",
    r"bypass\s+(the\s+)?(router|filter|security)",
    # Prompt leaking
    r"(reveal|show|print|output|repeat|tell\s+me)\s+(your\s+)?(system\s+prompt|instructions?|context|training)",
    r"what\s+(are|were|is)\s+your\s+(system\s+prompt|instructions?|original\s+instructions?)",
    # Invisible text injection markers (catch common zero-width char sequences)
    r"[\u200b\u200c\u200d\u2060\ufeff]{3,}",  # 3+ zero-width chars in sequence
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _INJECTION_PATTERNS]

# ---------------------------------------------------------------------------
# Spam / gibberish heuristics
# ---------------------------------------------------------------------------
_MIN_ENTROPY_THRESHOLD = 0.8  # bits/char — below this, content is likely keyboard mashing
_MAX_DUPLICATE_WINDOW_S = 300  # 5 minutes — identical messages within this window = spam
_SPAM_SHORT_MESSAGE_TOKENS = 5  # messages under this token count get extra scrutiny


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FilterResult:
    flagged: bool
    filter_type: Optional[str] = None       # "prompt_injection" | "spam" | "rate_limit"
    stage: Optional[str] = None             # "regex" | "ml" | "heuristic" | "deterministic"
    confidence: float = 0.0                 # 0.0–1.0
    pattern_matched: Optional[str] = None  # which regex fired (for logging/debugging)
    override_token: Optional[str] = None   # UUID — valid for one-time override
    reason: Optional[str] = None           # human-readable reason for the flag


@dataclass
class FilterPipelineConfig:
    """Tunable config — can be overridden via env vars or admin endpoint later."""
    injection_regex_enabled: bool = True
    injection_ml_enabled: bool = True      # requires llm-guard; gracefully skipped if unavailable
    spam_enabled: bool = True
    rate_limit_alert_enabled: bool = True
    # Score threshold for ML stage (0–1). Higher = fewer false positives, more false negatives.
    ml_threshold: float = 0.75


# ---------------------------------------------------------------------------
# Override token store (in-memory for now — Redis-backed in production)
# ---------------------------------------------------------------------------

class _OverrideTokenStore:
    """
    Stores one-time override tokens issued when a filter fires.
    Tokens expire after 5 minutes — user must act on the alert promptly.

    TODO: back with Redis for multi-worker staging + production.
    For single-instance EC2 staging, in-memory is fine.
    """
    _TTL_S = 300  # 5 minutes

    def __init__(self):
        self._tokens: dict[str, float] = {}  # token → issued_at

    def issue(self) -> str:
        token = str(uuid.uuid4())
        self._tokens[token] = time.time()
        self._cleanup()
        return token

    def consume(self, token: str) -> bool:
        """Returns True and removes the token if valid. Returns False if invalid/expired."""
        self._cleanup()
        if token in self._tokens:
            del self._tokens[token]
            return True
        return False

    def _cleanup(self):
        now = time.time()
        expired = [t for t, issued_at in self._tokens.items() if now - issued_at > self._TTL_S]
        for t in expired:
            del self._tokens[t]


_override_store = _OverrideTokenStore()


# ---------------------------------------------------------------------------
# ML scanner (optional — lazy-loaded if llm-guard is available)
# ---------------------------------------------------------------------------

_ml_scanner = None
_ml_scanner_load_attempted = False


def _get_ml_scanner():
    """
    Lazy-load the llm-guard PromptInjection scanner.
    Returns None if llm-guard is not installed or the model fails to load.
    We never fail loudly here — the regex stage is the load-bearing filter for MVP.
    """
    global _ml_scanner, _ml_scanner_load_attempted
    if _ml_scanner_load_attempted:
        return _ml_scanner
    _ml_scanner_load_attempted = True
    try:
        from llm_guard.input_scanners import PromptInjection
        _ml_scanner = PromptInjection()
        logger.info("✅ RouterFilter: llm-guard PromptInjection scanner loaded")
    except ImportError:
        logger.info("ℹ️  RouterFilter: llm-guard not installed — regex-only injection detection active")
    except Exception as e:
        logger.warning(f"⚠️  RouterFilter: llm-guard scanner failed to load: {e} — regex-only fallback")
    return _ml_scanner


# ---------------------------------------------------------------------------
# Duplicate message tracking (spam + rate_limit_abuse detection)
#
# Two distinct patterns, different windows and thresholds:
#
#   rate_limit_abuse:  ≥3 identical messages within 30s from same sender
#     → This is the "agent retry storm" pattern: an agent hitting errors and
#       retrying the same message floods context for everyone. Fires on msg 3.
#     → filter_type = "rate_limit_abuse"
#
#   spam (slow duplicate):  ≥2 identical messages within 5min from same sender
#     → User accidentally double-sends or copy-pastes the same message.
#     → filter_type = "spam"
#
# Acceptance criteria (@quantum_phoenix_307 TC):
#   Agent sends 5 identical messages in 30s → filter catches it by message 3.
# ---------------------------------------------------------------------------

_recent_messages: dict[str, list[float]] = {}  # content_hash → [timestamp, ...]
_DUPLICATE_WINDOW_ENTRIES = 50  # keep last N entries per hash to bound memory

# Rate-limit abuse detection: tight window, fires early
_RATE_LIMIT_ABUSE_WINDOW_S = 30   # 30-second rolling window
_RATE_LIMIT_ABUSE_THRESHOLD = 3   # flag on 3rd identical message in window

# Slow-duplicate spam detection: generous window, fires on 2nd
_SPAM_DUPLICATE_WINDOW_S = _MAX_DUPLICATE_WINDOW_S  # 5 minutes (existing constant)


def _content_hash(content: str, sender_id: Optional[str]) -> str:
    key = f"{sender_id or 'anon'}::{content.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _check_message_frequency(content: str, sender_id: Optional[str]) -> tuple[int, int]:
    """
    Record this message occurrence and return (count_30s, count_5min).
    count_30s: how many times this exact content+sender has been seen in the last 30s
    count_5min: how many times in the last 5 minutes (includes the current call)
    """
    h = _content_hash(content, sender_id)
    now = time.time()
    timestamps = _recent_messages.get(h, [])
    # Append current timestamp first, then count
    timestamps.append(now)
    # Keep only within 5-min window (longest window we care about)
    timestamps = [t for t in timestamps if now - t <= _SPAM_DUPLICATE_WINDOW_S]
    _recent_messages[h] = timestamps[-_DUPLICATE_WINDOW_ENTRIES:]

    count_30s = sum(1 for t in timestamps if now - t <= _RATE_LIMIT_ABUSE_WINDOW_S)
    count_5min = len(timestamps)
    return count_30s, count_5min


# ---------------------------------------------------------------------------
# Entropy check (gibberish detection without llm-guard)
# ---------------------------------------------------------------------------

def _shannon_entropy(text: str) -> float:
    """Compute Shannon entropy in bits/char. Keyboard mashing scores very low."""
    if not text:
        return 0.0
    import math
    freq = {}
    for c in text:
        freq[c] = freq.get(c, 0) + 1
    n = len(text)
    return -sum((count / n) * math.log2(count / n) for count in freq.values())


# ---------------------------------------------------------------------------
# Main filter pipeline
# ---------------------------------------------------------------------------

class RouterFilterPipeline:
    """
    Pre-routing filter pipeline. Call `scan()` before routing a message.

    Usage in router.py:
        result = router_filter_pipeline.scan(content, sender_id=str(user_id))
        if result.flagged:
            return filtered_routing_response(result)

    Thread-safe: uses no mutable state beyond the override store and duplicate tracker.
    """

    def __init__(self, config: Optional[FilterPipelineConfig] = None):
        self.config = config or FilterPipelineConfig()

    def validate_override(self, override_token: str) -> bool:
        """
        Check if an override token is valid and consume it.
        Returns True if the message should bypass the filter this once.
        """
        return _override_store.consume(override_token)

    def scan(
        self,
        content: str,
        sender_id: Optional[str] = None,
    ) -> FilterResult:
        """
        Run the full filter pipeline on `content`.
        Returns a FilterResult — check `.flagged` before routing.
        """
        if not content or not content.strip():
            return FilterResult(flagged=False)

        # ── Stage 1: Regex pre-filter ──────────────────────────────────────
        if self.config.injection_regex_enabled:
            result = self._scan_regex(content)
            if result.flagged:
                logger.warning(
                    f"🛡️ RouterFilter [regex]: injection detected "
                    f"pattern={result.pattern_matched!r} sender={sender_id}"
                )
                return result

        # ── Stage 2: ML classifier (optional) ─────────────────────────────
        if self.config.injection_ml_enabled:
            result = self._scan_ml(content)
            if result.flagged:
                logger.warning(
                    f"🛡️ RouterFilter [ml]: injection detected "
                    f"confidence={result.confidence:.2f} sender={sender_id}"
                )
                return result

        # ── Stage 3: Spam / gibberish heuristics ──────────────────────────
        if self.config.spam_enabled:
            result = self._scan_spam(content, sender_id)
            if result.flagged:
                logger.warning(
                    f"🛡️ RouterFilter [spam]: {result.reason} sender={sender_id}"
                )
                return result

        return FilterResult(flagged=False)

    def _scan_regex(self, content: str) -> FilterResult:
        for pattern in _COMPILED_PATTERNS:
            match = pattern.search(content)
            if match:
                return FilterResult(
                    flagged=True,
                    filter_type="prompt_injection",
                    stage="regex",
                    confidence=0.95,  # regex matches are high-confidence
                    pattern_matched=pattern.pattern[:60],
                    override_token=_override_store.issue(),
                    reason="Message matches known prompt injection pattern",
                )
        return FilterResult(flagged=False)

    def _scan_ml(self, content: str) -> FilterResult:
        scanner = _get_ml_scanner()
        if scanner is None:
            return FilterResult(flagged=False)
        try:
            _sanitized, is_valid, risk_score = scanner.scan("", content)
            if not is_valid and risk_score >= self.config.ml_threshold:
                return FilterResult(
                    flagged=True,
                    filter_type="prompt_injection",
                    stage="ml",
                    confidence=float(risk_score),
                    override_token=_override_store.issue(),
                    reason=f"ML classifier flagged as probable injection (score={risk_score:.2f})",
                )
        except Exception as e:
            logger.debug(f"RouterFilter: ML scan error (non-fatal): {e}")
        return FilterResult(flagged=False)

    def _scan_spam(self, content: str, sender_id: Optional[str]) -> FilterResult:
        stripped = content.strip()

        # Rate-limit abuse: ≥3 identical messages in 30s (agent retry storm pattern)
        # Fires on message 3 — allows 2 identical messages, blocks the 3rd and beyond.
        # This is the primary defense against context-flooding retry loops.
        count_30s, count_5min = _check_message_frequency(stripped, sender_id)

        if count_30s >= _RATE_LIMIT_ABUSE_THRESHOLD:
            return FilterResult(
                flagged=True,
                filter_type="rate_limit_abuse",
                stage="heuristic",
                confidence=0.98,
                override_token=_override_store.issue(),
                reason=(
                    f"Repeated identical message detected: {count_30s}x in 30s from the same sender. "
                    "This looks like an automated retry loop — back off and post once."
                ),
            )

        # Slow-duplicate spam: ≥2 identical messages in 5 minutes (accidental double-send)
        if count_5min >= 2:
            return FilterResult(
                flagged=True,
                filter_type="spam",
                stage="heuristic",
                confidence=0.9,
                override_token=_override_store.issue(),
                reason="Identical message sent within the last 5 minutes",
            )

        # Gibberish: very short content with low entropy
        tokens = stripped.split()
        entropy = _shannon_entropy(stripped)
        if len(tokens) <= _SPAM_SHORT_MESSAGE_TOKENS and entropy < _MIN_ENTROPY_THRESHOLD:
            return FilterResult(
                flagged=True,
                filter_type="spam",
                stage="heuristic",
                confidence=0.7,
                override_token=_override_store.issue(),
                reason=f"Content appears to be gibberish (entropy={entropy:.2f}, tokens={len(tokens)})",
            )

        return FilterResult(flagged=False)

    def scan_rate_limit_event(self, error: Exception) -> FilterResult:
        """
        Called when a rate limit exception fires in the classification path.
        Always returns a flagged result — zero false positives, deterministic detection.
        Used to surface a user alert (not block routing — the Gemini fallback handles that).
        """
        return FilterResult(
            flagged=True,
            filter_type="rate_limit",
            stage="deterministic",
            confidence=1.0,
            reason=f"AI classification temporarily unavailable — routing via fallback ({type(error).__name__})",
        )


# Module-level singleton
router_filter_pipeline = RouterFilterPipeline()
