export const SPACE_ROUTES = {
  collection: "/api/v1/spaces",
  item: (spaceId: string) => `/api/v1/spaces/${spaceId}`,
  members: (spaceId: string) => `/api/v1/spaces/${spaceId}/members`,
  switch: "/api/spaces/switch",
} as const;

export const TASK_ROUTES = {
  collection: "/api/v1/tasks",
  item: (taskId: string | number) => `/api/v1/tasks/${taskId}`,
  writeCollection: "/api/v1/tasks",
  writeItem: (taskId: string | number) => `/api/v1/tasks/${taskId}`,
  notes: (taskId: string | number) => `/api/v1/tasks/${taskId}/notes`,
  note: (taskId: string | number, noteId: string | number) =>
    `/api/v1/tasks/${taskId}/notes/${noteId}`,
} as const;

export const FLEET_CONTROL_ROUTES = {
  state: "/api/v1/fleet-control",
  enforcement: (surface: "agent_communication" | "reminders") =>
    `/api/v1/fleet-control/enforcement/${surface}`,
} as const;
