"""Backend-owned action definitions for governed interactive context artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SOURCE_LANE_TRUSTED = "trusted_named_action"
SOURCE_LANE_IN_WORLD = "in_world_proposal"

CONFIRMATION_NONE = "none"
CONFIRMATION_REQUIRED = "required"


class ActionPayloadValidationError(ValueError):
    """Raised when an action payload fails its backend-owned schema."""

    def __init__(self, action_set_id: str, action_id: str, errors: list[dict[str, Any]]):
        self.action_set_id = action_set_id
        self.action_id = action_id
        self.errors = errors
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ()))}: "
            f"{error.get('msg')} ({error.get('type')})"
            for error in errors
        )
        super().__init__(f"Invalid payload for {action_set_id}.{action_id}: {details}")


class UnknownActionError(KeyError):
    """Raised when an unknown action set or action id is requested."""


class _ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EmptyPayload(_ActionPayload):
    pass


class _OptionalCommentPayload(_ActionPayload):
    comment: str | None = Field(default=None, min_length=1, max_length=4000)


class _ReviewAnchor(_ActionPayload):
    artifact_version_id: str | None = Field(default=None, min_length=1)
    target_type: str = Field(min_length=1, max_length=100)
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    text_quote: str | None = Field(default=None, max_length=1000)


class _RequestChangesPayload(_ActionPayload):
    comment: str = Field(min_length=1, max_length=4000)
    anchors: list[_ReviewAnchor] = Field(default_factory=list, max_length=100)


class _TicTacToePlaceMarkPayload(_ActionPayload):
    row: int = Field(ge=0, le=2)
    col: int = Field(ge=0, le=2)
    mark: Literal["X", "O"]


@dataclass(frozen=True)
class InteractiveContextActionDefinition:
    """Immutable backend definition for one interactive context action."""

    action_set_id: str
    id: str
    canonical_label: str
    description: str
    source_lane: Literal["trusted_named_action", "in_world_proposal"]
    confirmation: Literal["none", "required"]
    trusted_lane_only: bool
    handler_name: str
    payload_model: type[BaseModel]

    @property
    def payload_schema(self) -> dict[str, Any]:
        return self.payload_model.model_json_schema()


def _action(
    *,
    action_set_id: str,
    action_id: str,
    canonical_label: str,
    description: str,
    source_lane: Literal["trusted_named_action", "in_world_proposal"],
    confirmation: Literal["none", "required"],
    trusted_lane_only: bool,
    handler_name: str,
    payload_model: type[BaseModel],
) -> InteractiveContextActionDefinition:
    return InteractiveContextActionDefinition(
        action_set_id=action_set_id,
        id=action_id,
        canonical_label=canonical_label,
        description=description,
        source_lane=source_lane,
        confirmation=confirmation,
        trusted_lane_only=trusted_lane_only,
        handler_name=handler_name,
        payload_model=payload_model,
    )


_ACTION_SETS: dict[str, dict[str, InteractiveContextActionDefinition]] = {
    "review.basic": {
        "approve": _action(
            action_set_id="review.basic",
            action_id="approve",
            canonical_label="Approve",
            description="Approve this artifact version.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_REQUIRED,
            trusted_lane_only=True,
            handler_name="review_basic_approve",
            payload_model=_OptionalCommentPayload,
        ),
        "reject": _action(
            action_set_id="review.basic",
            action_id="reject",
            canonical_label="Reject",
            description="Reject this artifact version.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_REQUIRED,
            trusted_lane_only=True,
            handler_name="review_basic_reject",
            payload_model=_OptionalCommentPayload,
        ),
        "request_changes": _action(
            action_set_id="review.basic",
            action_id="request_changes",
            canonical_label="Request changes",
            description="Request changes before this artifact can be approved.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_REQUIRED,
            trusted_lane_only=True,
            handler_name="review_basic_request_changes",
            payload_model=_RequestChangesPayload,
        ),
        "comment": _action(
            action_set_id="review.basic",
            action_id="comment",
            canonical_label="Comment",
            description="Add a review comment without final approval or rejection.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_NONE,
            trusted_lane_only=True,
            handler_name="review_basic_comment",
            payload_model=_RequestChangesPayload,
        ),
        "ok": _action(
            action_set_id="review.basic",
            action_id="ok",
            canonical_label="OK",
            description="Acknowledge the artifact or prompt.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_NONE,
            trusted_lane_only=True,
            handler_name="review_basic_ok",
            payload_model=_OptionalCommentPayload,
        ),
        "cancel": _action(
            action_set_id="review.basic",
            action_id="cancel",
            canonical_label="Cancel",
            description="Cancel this review flow without committing approval or rejection.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_NONE,
            trusted_lane_only=True,
            handler_name="review_basic_cancel",
            payload_model=_OptionalCommentPayload,
        ),
    },
    "game.tictactoe": {
        "place_mark": _action(
            action_set_id="game.tictactoe",
            action_id="place_mark",
            canonical_label="Place mark",
            description="Propose a mark placement on the board.",
            source_lane=SOURCE_LANE_IN_WORLD,
            confirmation=CONFIRMATION_NONE,
            trusted_lane_only=False,
            handler_name="game_tictactoe_place_mark",
            payload_model=_TicTacToePlaceMarkPayload,
        ),
        "new_game": _action(
            action_set_id="game.tictactoe",
            action_id="new_game",
            canonical_label="New game",
            description="Start a new tic-tac-toe game.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_REQUIRED,
            trusted_lane_only=True,
            handler_name="game_tictactoe_new_game",
            payload_model=_EmptyPayload,
        ),
        "resign": _action(
            action_set_id="game.tictactoe",
            action_id="resign",
            canonical_label="Resign",
            description="Resign the current game.",
            source_lane=SOURCE_LANE_TRUSTED,
            confirmation=CONFIRMATION_REQUIRED,
            trusted_lane_only=True,
            handler_name="game_tictactoe_resign",
            payload_model=_OptionalCommentPayload,
        ),
    },
}


def get_action_definition(action_set_id: str, action_id: str) -> InteractiveContextActionDefinition:
    try:
        return _ACTION_SETS[action_set_id][action_id]
    except KeyError as exc:
        raise UnknownActionError(f"Unknown interactive context action {action_set_id}.{action_id}") from exc


def list_action_definitions(action_set_id: str) -> Sequence[InteractiveContextActionDefinition]:
    try:
        return tuple(_ACTION_SETS[action_set_id].values())
    except KeyError as exc:
        raise UnknownActionError(f"Unknown interactive context action set {action_set_id}") from exc


def serialize_host_action(
    action: InteractiveContextActionDefinition,
    *,
    artifact_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the host-rendered descriptor, ignoring artifact-provided semantics."""
    _ = artifact_request
    return {
        "action_set_id": action.action_set_id,
        "id": action.id,
        "canonical_label": action.canonical_label,
        "label": action.canonical_label,
        "description": action.description,
        "source_lane": action.source_lane,
        "confirmation": action.confirmation,
        "trusted_lane_only": action.trusted_lane_only,
        "payload_schema": action.payload_schema,
    }


def validate_action_payload(action_set_id: str, action_id: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    action = get_action_definition(action_set_id, action_id)
    try:
        parsed = action.payload_model.model_validate(payload or {})
    except ValidationError as exc:
        raise ActionPayloadValidationError(action_set_id, action_id, exc.errors()) from exc
    return parsed.model_dump(exclude_none=True, exclude_unset=True)
