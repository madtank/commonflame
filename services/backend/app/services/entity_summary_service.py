"""Canonical Bedrock-backed leaf summaries for non-message entities."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.core.context_payload import serialize_value_preview

logger = logging.getLogger(__name__)

BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_LEAF_MODEL = os.getenv(
    "BEDROCK_LEAF_SUMMARY_MODEL",
    os.getenv(
        "BEDROCK_MESSAGE_SUMMARY_MODEL",
        os.getenv("BEDROCK_SUMMARY_MODEL", "us.amazon.nova-micro-v1:0"),
    ),
)

_bedrock_client = None


def _get_bedrock_client():
    global _bedrock_client
    if os.getenv("ENABLE_CLOUD_AI", "false").lower() != "true":
        return None
    if _bedrock_client is None:
        try:
            import boto3

            _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to initialize Bedrock leaf summary client: %s", exc)
    return _bedrock_client


def _clean_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", " ", text)
    text = re.sub(r"(^|\n)\s*[-*+]\s+", " ", text)
    text = re.sub(r"(^|\n)\s*\d+\.\s+", " ", text)
    text = re.sub(r"[*_~>#]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_chars(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,;:-") + "..."


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(" ,;:-") + "..."


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip() or text


def _humanize_key(key: str) -> str:
    cleaned = re.sub(r"[_:/-]+", " ", key or "").strip()
    return re.sub(r"\s+", " ", cleaned)


def _serialize_value(value: Any) -> str:
    return serialize_value_preview(value)


async def _invoke_leaf_summary(prompt: str, *, max_tokens: int = 96) -> str | None:
    client = _get_bedrock_client()
    if not client:
        return None

    body = json.dumps(
        {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.1},
        }
    )

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.invoke_model(
                modelId=BEDROCK_LEAF_MODEL,
                contentType="application/json",
                accept="application/json",
                body=body,
            ),
        )
        payload = json.loads(response["body"].read())
        text = payload["output"]["message"]["content"][0]["text"]
        cleaned = _clean_text(text)
        return cleaned or None
    except Exception as exc:  # pragma: no cover - network/provider failure
        logger.warning("Leaf summary generation failed: %s", exc)
        return None


def _summary_payload(summary: str, model_id: str, *, field_name: str = "summary") -> dict[str, str]:
    return {
        field_name: summary,
        "summary_model": model_id,
        "summary_generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def summarize_task_leaf(
    *, title: str, description: str | None = None, requirements: Any = None
) -> dict[str, str] | None:
    prompt = (
        "Summarize this task for a compact task list row. "
        "Return one plain-text sentence, no markdown, no bullets, max 20 words.\n\n"
        f"Title: {title}\n"
        f"Description: {_serialize_value(description) if description else '(none)'}\n"
        f"Requirements: {_serialize_value(requirements) if requirements else '(none)'}"
    )
    summary = await _invoke_leaf_summary(prompt)
    if not summary:
        return None
    return _summary_payload(
        _truncate_words(_first_sentence(summary), 20),
        BEDROCK_LEAF_MODEL,
        field_name="ai_summary",
    )


async def summarize_context_leaf(*, key: str, value: Any, topic: str | None = None) -> dict[str, str] | None:
    prompt = (
        "Summarize this shared context entry for a compact list row. "
        "Return one plain-text sentence, no markdown, max 96 characters.\n\n"
        f"Key: {key}\n"
        f"Topic: {topic or '(none)'}\n"
        f"Value: {_serialize_value(value)}"
    )
    summary = await _invoke_leaf_summary(prompt, max_tokens=64)
    if not summary:
        return None
    return _summary_payload(_truncate_chars(_first_sentence(summary), 96), BEDROCK_LEAF_MODEL)
