"""Canonical backend API routes used by MCP tools.

Keep route strings here when a path is shared across tools. This avoids the
old `/api/spaces/` versus `/api/v1/spaces` drift that caused widget 404s.
"""

SPACES_COLLECTION_PATH = "/api/v1/spaces"
SPACES_SWITCH_PATH = SPACES_COLLECTION_PATH + "/switch"

TASKS_COLLECTION_PATH = "/api/v1/tasks"
TASKS_WRITE_COLLECTION_PATH = "/api/v1/tasks"


def space_item_path(space_id: str) -> str:
    return f"{SPACES_COLLECTION_PATH}/{space_id}"


def space_members_path(space_id: str) -> str:
    return f"{space_item_path(space_id)}/members"


def task_read_item_path(task_id: str) -> str:
    return f"{TASKS_COLLECTION_PATH}/{task_id}"


def task_write_item_path(task_id: str) -> str:
    return f"{TASKS_WRITE_COLLECTION_PATH}/{task_id}"
