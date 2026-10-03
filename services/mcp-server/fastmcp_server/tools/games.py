"""Experimental game tool for MCP App turn-taking.

The v0 contract intentionally keeps game state as context data. The MCP tool
creates, reads, and mutates that state through the backend context API; the
widget is only a renderer/control surface.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal, Optional
from urllib.parse import quote
from uuid import uuid4

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.tools.tool import ToolResult
from starlette.requests import Request

from fastmcp_server.api_client import (
    api_request_with_context,
    extract_agent_context,
    user_request_space_params,
    user_request_space_payload,
)
from fastmcp_server.mcp_ui import (
    bounded_write_annotations,
    build_action,
    build_notice,
    build_tool_output,
    tool_app_config,
    tool_meta,
    widget_tool_result,
)

GAME_CONTEXT_PREFIX = "game:"
GAME_CONTEXT_TTL_SECONDS = 60 * 60 * 24
TICTACTOE_GAME = "tic_tac_toe"
TRIVIA_GAME = "ax_trivia"
BOARD_SIZE = 3
CELL_COLUMNS = ("a", "b", "c")
WIN_LINES = (
    ((0, 0), (0, 1), (0, 2)),
    ((1, 0), (1, 1), (1, 2)),
    ((2, 0), (2, 1), (2, 2)),
    ((0, 0), (1, 0), (2, 0)),
    ((0, 1), (1, 1), (2, 1)),
    ((0, 2), (1, 2), (2, 2)),
    ((0, 0), (1, 1), (2, 2)),
    ((0, 2), (1, 1), (2, 0)),
)
AX_TRIVIA_QUESTIONS = [
    {
        "id": "ax-northstar",
        "prompt": "What is the North Star surface for rich Waystation interactions?",
        "choices": ["MCP Apps/widgets", "Raw JSON in chat", "Email-only workflows", "Hidden logs"],
        "answer_index": 0,
        "explanation": "MCP Apps/widgets are the rich app surface; transcript cards stay compact.",
    },
    {
        "id": "ax-context",
        "prompt": "What does context storage give agents?",
        "choices": ["Durable shared artifacts", "Only temporary stdout", "Browser cookies", "DNS records"],
        "answer_index": 0,
        "explanation": "Context is the durable shared layer for artifacts, specs, and state.",
    },
    {
        "id": "ax-hitl",
        "prompt": "What should a draft flow require before creating an agent or space?",
        "choices": ["Human approval", "Silent execution", "A page reload", "A new database"],
        "answer_index": 0,
        "explanation": "Draft widgets are human-in-the-loop: the agent prepares, the user approves.",
    },
]


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_handle(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lstrip("@")


def _actor_handle(ctx: dict[str, Any], explicit: str | None = None) -> str:
    return (
        _normalize_handle(explicit)
        or _normalize_handle(ctx.get("agent_name"))
        or _normalize_handle(ctx.get("username"))
        or _normalize_handle(ctx.get("email"))
        or "player"
    )


def _context_key(game_id: str, game: str = TICTACTOE_GAME) -> str:
    return f"{GAME_CONTEXT_PREFIX}{game}:{game_id}"


def _empty_board() -> list[list[str | None]]:
    return [[None for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]


def _player(mark: str, handle: str) -> dict[str, str]:
    return {"mark": mark, "id": handle, "handle": handle, "display_name": handle}


def _trivia_player(handle: str) -> dict[str, str]:
    return {"id": handle, "handle": handle, "display_name": handle}


def _source_text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return json.dumps(value, indent=2, sort_keys=True)
    for key in ("markdown", "content", "text", "body", "summary"):
        inner = value.get(key)
        if isinstance(inner, str) and inner.strip():
            return inner
    if isinstance(value.get("value"), (dict, str)):
        return _source_text_from_value(value["value"])
    return json.dumps(value, indent=2, sort_keys=True)


def _source_title_from_value(source_key: str | None, value: Any, text: str) -> str:
    if isinstance(value, dict):
        for key in ("title", "name", "query"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:120]
    match = re.search(r"^\s*#\s+(.+)$", text, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()[:120]
    return source_key or "Waystation trivia source"


def _source_links_from_value(value: Any) -> list[str]:
    if isinstance(value, dict) and isinstance(value.get("links"), list):
        return [str(link) for link in value["links"][:6] if str(link).strip()]
    return []


def _source_payload(source_key: str | None, value: Any) -> dict[str, Any]:
    text = _source_text_from_value(value)
    return {
        "key": source_key,
        "title": _source_title_from_value(source_key, value, text),
        "text": text[:10000],
        "excerpt": text[:900],
        "links": _source_links_from_value(value),
        "schema": "research" if isinstance(value, dict) and "summary" in value else "markdown",
    }


def _markdown_headings(text: str) -> list[str]:
    headings = []
    for match in re.finditer(r"^\s{0,3}#{1,3}\s+(.+)$", text, flags=re.MULTILINE):
        heading = re.sub(r"\s+#*$", "", match.group(1)).strip()
        if heading and heading not in headings:
            headings.append(heading)
    return headings[:6]


def _source_terms(text: str) -> list[str]:
    checks = [
        ("MCP Apps/widgets", r"\b(mcp apps?|widgets?)\b"),
        ("shared context", r"\bcontext\b"),
        ("human-in-the-loop drafts", r"\b(draft|human[- ]in[- ]the[- ]loop|hitl)\b"),
        ("agent coordination", r"\bagents?\b"),
        ("signals and alerts", r"\b(signal|alert)\b"),
    ]
    found = []
    for label, pattern in checks:
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(label)
    return found


def _question(
    question_id: str,
    prompt: str,
    correct: str,
    distractors: list[str],
    explanation: str,
) -> dict[str, Any]:
    choices = [correct, *[item for item in distractors if item != correct]][:4]
    while len(choices) < 4:
        choices.append(["MCP Apps/widgets", "shared context", "human approval", "agent coordination"][len(choices) % 4])
    return {
        "id": question_id,
        "prompt": prompt,
        "choices": choices,
        "answer_index": 0,
        "explanation": explanation,
    }


def _trivia_questions_from_source(source: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not source:
        return [dict(item) for item in AX_TRIVIA_QUESTIONS]

    text = str(source.get("text") or "")
    headings = _markdown_headings(text)
    terms = _source_terms(text)
    questions: list[dict[str, Any]] = []
    if headings:
        questions.append(
            _question(
                "source-heading",
                "Which section appears in the loaded source?",
                headings[0],
                ["Billing setup", "Mobile sync", "DNS propagation"],
                "The question was generated from the Markdown headings in the stored source.",
            )
        )
    if terms:
        questions.append(
            _question(
                "source-concept",
                "Which Waystation concept is emphasized by this source?",
                terms[0],
                ["ad auctions", "spreadsheet macros", "raw DNS records"],
                "The source was parsed for recurring Waystation product concepts.",
            )
        )
    if source.get("links"):
        questions.append(
            _question(
                "source-links",
                "What kind of supporting evidence did this source preserve?",
                "links",
                ["database passwords", "browser cookies", "private keys"],
                "Research context keeps source links with the stored summary.",
            )
        )

    for fallback in AX_TRIVIA_QUESTIONS:
        if len(questions) >= 3:
            break
        if all(existing["id"] != fallback["id"] for existing in questions):
            questions.append(dict(fallback))
    return questions[:3]


def _new_trivia_state(
    *,
    game_id: str,
    player: str,
    created_by: str,
    space_id: str | None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = _now()
    questions = _trivia_questions_from_source(source)
    return {
        "kind": "game_state",
        "game": TRIVIA_GAME,
        "version": 1,
        "game_id": game_id,
        "context_key": _context_key(game_id, TRIVIA_GAME),
        "space_id": space_id,
        "render": {
            "mode": "quiz",
            "renderer": "trivia",
            "views": ["game", "source", "code", "events"],
        },
        "players": [_trivia_player(player)],
        "turn": {"player": player},
        "status": "active",
        "score": 0,
        "current_question_index": 0,
        "questions": questions,
        "answers": [],
        "source": source,
        "created_at": created_at,
        "updated_at": created_at,
        "events": [
            {
                "type": "game_created",
                "turn": 0,
                "actor": created_by,
                "at": created_at,
                "summary": f"Waystation trivia started for {player}.",
            }
        ],
    }


def _new_game_state(
    *,
    game_id: str,
    player_x: str,
    player_o: str,
    created_by: str,
    space_id: str | None,
) -> dict[str, Any]:
    created_at = _now()
    return {
        "kind": "game_state",
        "game": "tic_tac_toe",
        "version": 1,
        "game_id": game_id,
        "context_key": _context_key(game_id),
        "space_id": space_id,
        "render": {
            "mode": "grid",
            "renderer": "tic_tac_toe",
            "width": BOARD_SIZE,
            "height": BOARD_SIZE,
            "views": ["game", "code", "events"],
        },
        "players": [_player("X", player_x), _player("O", player_o)],
        "turn": {"mark": "X", "player": player_x},
        "board": _empty_board(),
        "status": "active",
        "winner": None,
        "winning_line": [],
        "move_number": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "events": [
            {
                "type": "game_created",
                "turn": 0,
                "actor": created_by,
                "at": created_at,
                "summary": f"Tic-tac-toe started: {player_x} vs {player_o}.",
            }
        ],
    }


def _unwrap_context_value(result: dict[str, Any]) -> dict[str, Any] | None:
    value = result.get("value")
    if isinstance(value, dict) and isinstance(value.get("value"), dict):
        return value["value"]
    if isinstance(value, dict):
        return value
    if isinstance(result.get("data"), dict):
        return result["data"]
    if isinstance(result.get("item"), dict):
        item = result["item"]
        if isinstance(item.get("value"), dict):
            return item["value"]
    return None


def _coerce_board(board: Any) -> list[list[str | None]]:
    if not isinstance(board, list) or len(board) != BOARD_SIZE:
        return _empty_board()
    coerced: list[list[str | None]] = []
    for row in board:
        if not isinstance(row, list) or len(row) != BOARD_SIZE:
            return _empty_board()
        coerced.append([cell if cell in {"X", "O"} else None for cell in row])
    return coerced


def _normalize_tictactoe_state(value: dict[str, Any]) -> dict[str, Any]:
    state = dict(value)
    state.setdefault("game", TICTACTOE_GAME)
    state["board"] = _coerce_board(state.get("board"))
    if not isinstance(state.get("events"), list):
        state["events"] = []
    if not isinstance(state.get("players"), list) or len(state["players"]) != 2:
        state["players"] = [_player("X", "player_x"), _player("O", "player_o")]
    state.setdefault("render", {
        "mode": "grid",
        "renderer": "tic_tac_toe",
        "width": BOARD_SIZE,
        "height": BOARD_SIZE,
        "views": ["game", "code", "events"],
    })
    state.setdefault("turn", {"mark": "X", "player": _player_for_mark(state, "X")})
    state.setdefault("status", "active")
    state.setdefault("winner", None)
    state.setdefault("winning_line", [])
    state.setdefault("move_number", len([e for e in state["events"] if e.get("type") == "move_applied"]))
    return state


def _normalize_question(question: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(question, dict):
        return None
    choices = question.get("choices")
    if not isinstance(choices, list) or len(choices) < 2:
        return None
    normalized_choices = [str(choice) for choice in choices[:4]]
    try:
        answer_index = int(question.get("answer_index", 0))
    except (TypeError, ValueError):
        answer_index = 0
    if answer_index < 0 or answer_index >= len(normalized_choices):
        answer_index = 0
    return {
        "id": str(question.get("id") or f"q{index + 1}"),
        "prompt": str(question.get("prompt") or f"Question {index + 1}"),
        "choices": normalized_choices,
        "answer_index": answer_index,
        "explanation": str(question.get("explanation") or ""),
    }


def _normalize_trivia_state(value: dict[str, Any]) -> dict[str, Any]:
    state = dict(value)
    state["game"] = TRIVIA_GAME
    if not isinstance(state.get("events"), list):
        state["events"] = []
    if not isinstance(state.get("answers"), list):
        state["answers"] = []
    questions = [
        normalized
        for index, question in enumerate(state.get("questions") or [])
        if (normalized := _normalize_question(question, index))
    ]
    state["questions"] = questions or [dict(item) for item in AX_TRIVIA_QUESTIONS]
    if not isinstance(state.get("players"), list) or not state["players"]:
        state["players"] = [_trivia_player("player")]
    try:
        current_index = int(state.get("current_question_index") or 0)
    except (TypeError, ValueError):
        current_index = 0
    state["current_question_index"] = max(0, min(current_index, len(state["questions"])))
    state["score"] = len([answer for answer in state["answers"] if answer.get("correct")])
    if state["current_question_index"] >= len(state["questions"]):
        state["status"] = "complete"
        state["turn"] = None
    else:
        state.setdefault("status", "active")
        state.setdefault("turn", {"player": state["players"][0].get("handle", "player")})
    state.setdefault(
        "render",
        {
            "mode": "quiz",
            "renderer": "trivia",
            "views": ["game", "source", "code", "events"],
        },
    )
    return state


def _normalize_state(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("game") == TRIVIA_GAME:
        return _normalize_trivia_state(value)
    return _normalize_tictactoe_state(value)


def _player_for_mark(state: dict[str, Any], mark: str) -> str:
    for player in state.get("players", []):
        if isinstance(player, dict) and player.get("mark") == mark:
            return _normalize_handle(player.get("handle") or player.get("id"))
    return "player"


def _mark_for_player(state: dict[str, Any], player_handle: str) -> str | None:
    normalized = _normalize_handle(player_handle)
    for player in state.get("players", []):
        if not isinstance(player, dict):
            continue
        candidates = {
            _normalize_handle(player.get("id")),
            _normalize_handle(player.get("handle")),
            _normalize_handle(player.get("display_name")),
        }
        if normalized in candidates:
            return player.get("mark") if player.get("mark") in {"X", "O"} else None
    return None


def _parse_cell(cell: str | None) -> tuple[int, int] | None:
    cleaned = (cell or "").strip().lower()
    if len(cleaned) != 2:
        return None
    col, row = cleaned[0], cleaned[1]
    if col not in CELL_COLUMNS or row not in {"1", "2", "3"}:
        return None
    return int(row) - 1, CELL_COLUMNS.index(col)


def _cell_name(row: int, col: int) -> str:
    return f"{CELL_COLUMNS[col]}{row + 1}"


def _legal_cells(board: list[list[str | None]]) -> list[str]:
    return [
        _cell_name(row, col)
        for row in range(BOARD_SIZE)
        for col in range(BOARD_SIZE)
        if board[row][col] is None
    ]


def _winner(board: list[list[str | None]]) -> tuple[str | None, list[str]]:
    for line in WIN_LINES:
        values = [board[row][col] for row, col in line]
        if values[0] and values[0] == values[1] == values[2]:
            return values[0], [_cell_name(row, col) for row, col in line]
    return None, []


def _apply_move(
    state: dict[str, Any],
    *,
    player: str,
    cell: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _normalize_state(state)
    if state.get("status") != "active":
        return state, build_notice("This game is already complete.", severity="warning", code="game_complete")

    parsed = _parse_cell(cell)
    if parsed is None:
        return state, build_notice("Move must be a cell from a1 through c3.", severity="error", code="invalid_cell")

    board = state["board"]
    row, col = parsed
    if board[row][col] is not None:
        return state, build_notice(f"Cell {cell} is already occupied.", severity="error", code="cell_occupied")

    current_mark = state.get("turn", {}).get("mark")
    if current_mark not in {"X", "O"}:
        current_mark = "X"
    player_mark = _mark_for_player(state, player)
    if player_mark and player_mark != current_mark:
        return state, build_notice(
            f"It is {_player_for_mark(state, current_mark)}'s turn.",
            severity="error",
            code="not_players_turn",
        )
    if not player_mark:
        # V0 game widgets run in open-turn mode: the board may submit the
        # current turn without a signed player identity. Backend-owned game
        # APIs can tighten identity binding later without changing the app UI.
        player = _player_for_mark(state, current_mark)

    board[row][col] = current_mark
    winner, winning_line = _winner(board)
    move_number = int(state.get("move_number") or 0) + 1
    next_mark = "O" if current_mark == "X" else "X"
    status = "active"
    if winner:
        status = "complete"
    elif not _legal_cells(board):
        status = "draw"

    updated_at = _now()
    state.update(
        {
            "board": board,
            "move_number": move_number,
            "status": status,
            "winner": winner,
            "winning_line": winning_line,
            "updated_at": updated_at,
            "turn": {
                "mark": next_mark,
                "player": _player_for_mark(state, next_mark),
            }
            if status == "active"
            else None,
        }
    )
    state["events"].append(
        {
            "type": "move_applied",
            "turn": move_number,
            "actor": player,
            "mark": current_mark,
            "cell": _cell_name(row, col),
            "at": updated_at,
            "status": status,
            "winner": winner,
        }
    )
    if winner:
        notice = build_notice(f"{_player_for_mark(state, winner)} wins.", code="game_won")
    elif status == "draw":
        notice = build_notice("Game ended in a draw.", code="game_draw")
    else:
        notice = build_notice(f"Move applied. {_player_for_mark(state, next_mark)} is next.", code="move_applied")
    return state, notice


async def _load_game(ctx: dict[str, Any], game_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for game in (TICTACTOE_GAME, TRIVIA_GAME):
        key = _context_key(game_id, game)
        result = await api_request_with_context(
            ctx,
            "GET",
            f"/api/v1/context/{quote(key, safe='')}",
            params=user_request_space_params(ctx),
        )
        if result.get("error"):
            continue
        value = _unwrap_context_value(result)
        if isinstance(value, dict):
            return _normalize_state(value), None
        return None, build_notice(
            f"Game {game_id} did not contain valid game state.",
            severity="error",
            code="invalid_game_state",
        )
    return None, build_notice(f"Game {game_id} was not found.", severity="error", code="game_not_found")


async def _load_context_source(ctx: dict[str, Any], source_key: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not source_key:
        return None, None
    result = await api_request_with_context(
        ctx,
        "GET",
        f"/api/v1/context/{quote(source_key, safe='')}",
        params=user_request_space_params(ctx),
    )
    if result.get("error"):
        return None, build_notice(
            f"Could not load context source {source_key}.",
            severity="warning",
            code="source_not_found",
        )
    value = _unwrap_context_value(result)
    if value is None:
        return None, build_notice(
            f"Context source {source_key} was empty.",
            severity="warning",
            code="source_empty",
        )
    return _source_payload(source_key, value), None


async def _store_game(ctx: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return await api_request_with_context(
        ctx,
        "POST",
        "/api/v1/context",
        json_data=user_request_space_payload(
            ctx,
            {
                "key": state["context_key"],
                "value": state,
                "ttl": GAME_CONTEXT_TTL_SECONDS,
                "topic": "game",
            },
        ),
    )


def _game_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    if state.get("status") != "active":
        return []
    if state.get("game") == TRIVIA_GAME:
        question = _current_trivia_question(state)
        if not question:
            return []
        game_id = str(state.get("game_id") or "")
        return [
            build_action(
                f"answer-{index}",
                str(choice),
                kind="tool",
                target="games",
                args={
                    "action": "answer",
                    "game_id": game_id,
                    "choice": index,
                },
                enabled=True,
                idempotent=False,
            )
            for index, choice in enumerate(question.get("choices", []))
        ]
    game_id = str(state.get("game_id") or "")
    turn = state.get("turn") or {}
    player = turn.get("player")
    return [
        build_action(
            f"move-{cell}",
            f"Move {cell}",
            kind="tool",
            target="games",
            args={
                "action": "move",
                "game_id": game_id,
                "cell": cell,
                "player": player,
            },
            enabled=True,
            idempotent=False,
        )
        for cell in _legal_cells(state.get("board", []))
    ]


def _current_trivia_question(state: dict[str, Any]) -> dict[str, Any] | None:
    state = _normalize_trivia_state(state)
    index = int(state.get("current_question_index") or 0)
    questions = state.get("questions") or []
    if index < 0 or index >= len(questions):
        return None
    return questions[index]


def _public_game_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("game") != TRIVIA_GAME:
        return state
    public = dict(state)
    public["questions"] = [
        {
            key: value
            for key, value in question.items()
            if key != "answer_index"
        }
        for question in state.get("questions", [])
        if isinstance(question, dict)
    ]
    return public


def _parse_choice(choice: Any, answer: str | None, question: dict[str, Any]) -> int | None:
    if choice is not None:
        try:
            parsed = int(choice)
            if 0 <= parsed < len(question.get("choices", [])):
                return parsed
        except (TypeError, ValueError):
            return None
    if answer:
        normalized = answer.strip().casefold()
        for index, item in enumerate(question.get("choices", [])):
            if str(item).strip().casefold() == normalized:
                return index
    return None


def _apply_trivia_answer(
    state: dict[str, Any],
    *,
    player: str,
    choice: Any,
    answer: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _normalize_trivia_state(state)
    if state.get("status") != "active":
        return state, build_notice("This trivia game is already complete.", severity="warning", code="game_complete")

    question = _current_trivia_question(state)
    if not question:
        state["status"] = "complete"
        state["turn"] = None
        return state, build_notice("Trivia complete.", code="trivia_complete")

    choice_index = _parse_choice(choice, answer, question)
    if choice_index is None:
        return state, build_notice("Choose one of the listed answers.", severity="error", code="invalid_answer")

    correct_index = int(question.get("answer_index") or 0)
    correct = choice_index == correct_index
    updated_at = _now()
    turn_number = len(state["answers"]) + 1
    selected = str(question["choices"][choice_index])
    state["answers"].append(
        {
            "question_id": question["id"],
            "choice_index": choice_index,
            "choice": selected,
            "correct": correct,
            "actor": player,
            "at": updated_at,
        }
    )
    state["events"].append(
        {
            "type": "answer_submitted",
            "turn": turn_number,
            "actor": player,
            "question_id": question["id"],
            "choice": selected,
            "correct": correct,
            "at": updated_at,
            "summary": f"{player} answered {'correctly' if correct else 'incorrectly'}.",
        }
    )
    state["score"] = int(state.get("score") or 0) + (1 if correct else 0)
    state["current_question_index"] = int(state.get("current_question_index") or 0) + 1
    state["updated_at"] = updated_at
    if state["current_question_index"] >= len(state["questions"]):
        state["status"] = "complete"
        state["turn"] = None
        message = f"Trivia complete. Score: {state['score']}/{len(state['questions'])}."
        return state, build_notice(message, code="trivia_complete")
    state["turn"] = {"player": player}
    severity = "info" if correct else "warning"
    message = question.get("explanation") or ("Correct." if correct else "Not quite.")
    return state, build_notice(message, severity=severity, code="answer_recorded")


def _game_result(
    state: dict[str, Any],
    *,
    action: str,
    notice: dict[str, Any] | None = None,
) -> ToolResult:
    state = _normalize_state(state)
    legal_moves = []
    if state.get("game") == TICTACTOE_GAME and state.get("status") == "active":
        legal_moves = _legal_cells(state["board"])
    current_question = None
    if state.get("game") == TRIVIA_GAME and state.get("status") == "active":
        question = _current_trivia_question(state)
        if question:
            current_question = {
                key: value
                for key, value in question.items()
                if key != "answer_index"
            }
    data = {
        "game": _public_game_state(state),
        "legal_moves": legal_moves,
        "current_question": current_question,
    }
    structured = build_tool_output(
        "game_state",
        1,
        state.get("status", "active"),
        data,
        actions=_game_actions(state),
    )
    if notice:
        structured["notice"] = notice
    return widget_tool_result(
        "games",
        action=action,
        content=f"{state.get('game', 'game')} {state.get('game_id')}: {state.get('status')}",
        structured_content=structured,
    )


def register_games_tool(mcp: FastMCP):
    @mcp.tool(
        annotations=bounded_write_annotations(),
        app=tool_app_config("games"),
        meta=tool_meta("games"),
    )
    async def games(
        action: Literal["create", "create_trivia", "get", "move", "answer"] = "create",
        game_id: Optional[str] = None,
        player_x: Optional[str] = None,
        player_o: Optional[str] = None,
        player: Optional[str] = None,
        cell: Optional[str] = None,
        source_key: Optional[str] = None,
        choice: Optional[int] = None,
        answer: Optional[str] = None,
        # Accepted for MCP schema parity; request context is resolved centrally.
        space_id: Optional[str] = None,
        # DI params (hidden from MCP schema):
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Create and play small turn-based games through MCP Apps.

        Actions:
        - create: Create a Tic-Tac-Toe game run backed by context state.
        - create_trivia: Create an Waystation trivia game from built-in questions or a context source.
        - get: Load an existing game by game_id.
        - move: Apply a legal move to an active game (requires game_id and cell).
        - answer: Answer the current trivia question (requires game_id and choice).
        """
        ctx = extract_agent_context(token, request)
        actor = _actor_handle(ctx, player)

        if action == "create":
            created_id = game_id or uuid4().hex[:12]
            x_handle = _actor_handle(ctx, player_x or actor)
            o_handle = _normalize_handle(player_o) or "open-player"
            state = _new_game_state(
                game_id=created_id,
                player_x=x_handle,
                player_o=o_handle,
                created_by=actor,
                space_id=ctx.get("space_id"),
            )
            result = await _store_game(ctx, state)
            if result.get("error"):
                return {
                    "error": "Could not create game",
                    "detail": result.get("detail") or result.get("error"),
                }
            return _game_result(
                state,
                action=action,
                notice=build_notice("Game created.", code="game_created"),
            )

        if action == "create_trivia":
            created_id = game_id or uuid4().hex[:12]
            source, source_notice = await _load_context_source(ctx, source_key)
            state = _new_trivia_state(
                game_id=created_id,
                player=actor,
                created_by=actor,
                space_id=ctx.get("space_id"),
                source=source,
            )
            result = await _store_game(ctx, state)
            if result.get("error"):
                return {
                    "error": "Could not create trivia game",
                    "detail": result.get("detail") or result.get("error"),
                }
            return _game_result(
                state,
                action=action,
                notice=source_notice or build_notice("Trivia game created.", code="trivia_created"),
            )

        if action == "get":
            if not game_id:
                return {"error": "'game_id' required for get action"}
            state, notice = await _load_game(ctx, game_id)
            if state is None:
                fallback = _new_game_state(
                    game_id=game_id,
                    player_x="player_x",
                    player_o="player_o",
                    created_by=actor,
                    space_id=ctx.get("space_id"),
                )
                fallback["status"] = "error"
                return _game_result(fallback, action=action, notice=notice)
            return _game_result(state, action=action)

        if action == "answer":
            if not game_id:
                return {"error": "'game_id' required for answer action"}
            state, notice = await _load_game(ctx, game_id)
            if state is None:
                fallback = _new_trivia_state(
                    game_id=game_id,
                    player=actor,
                    created_by=actor,
                    space_id=ctx.get("space_id"),
                )
                fallback["status"] = "error"
                return _game_result(fallback, action=action, notice=notice)
            if state.get("game") != TRIVIA_GAME:
                return _game_result(
                    state,
                    action=action,
                    notice=build_notice(
                        "This game does not accept trivia answers.",
                        severity="error",
                        code="wrong_game_type",
                    ),
                )

            next_state, answer_notice = _apply_trivia_answer(state, player=actor, choice=choice, answer=answer)
            if answer_notice.get("severity") == "error":
                return _game_result(next_state, action=action, notice=answer_notice)
            result = await _store_game(ctx, next_state)
            if result.get("error"):
                return {
                    "error": "Could not persist answer",
                    "detail": result.get("detail") or result.get("error"),
                }
            return _game_result(next_state, action=action, notice=answer_notice)

        if action == "move":
            if not game_id:
                return {"error": "'game_id' required for move action"}
            state, notice = await _load_game(ctx, game_id)
            if state is None:
                fallback = _new_game_state(
                    game_id=game_id,
                    player_x="player_x",
                    player_o="player_o",
                    created_by=actor,
                    space_id=ctx.get("space_id"),
                )
                fallback["status"] = "error"
                return _game_result(fallback, action=action, notice=notice)

            next_state, move_notice = _apply_move(state, player=actor, cell=cell)
            if move_notice.get("severity") == "error":
                return _game_result(next_state, action=action, notice=move_notice)
            result = await _store_game(ctx, next_state)
            if result.get("error"):
                return {
                    "error": "Could not persist move",
                    "detail": result.get("detail") or result.get("error"),
                }
            return _game_result(next_state, action=action, notice=move_notice)

        return {"error": "Unknown action. Available: create, create_trivia, get, move, answer"}
