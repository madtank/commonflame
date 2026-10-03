import { describe, expect, it } from "vitest";

import { buildChatInputSendOptions } from "./ChatInput";

describe("ChatInput forward metadata", () => {
  it("builds source-card metadata for posts and reply paths", () => {
    expect(
      buildChatInputSendOptions({
        mentionedAgentIds: ["agent-react"],
        forwardingCard: {
          cardId: "card-task-1",
          cardType: "task",
          sourceMessageId: "message-1",
          title: "Task needs review",
          summary: "Review the deploy checklist",
          resourceType: "task",
          resourceId: "task-1",
          resourceUri: "ui://tasks/detail/task-1",
          taskId: "task-1",
        },
      }),
    ).toEqual({
      mentionedAgentIds: ["agent-react"],
      metadata: {
        forward: {
          intent: "share",
          source_card_id: "card-task-1",
          card_type: "task",
          source_message_id: "message-1",
          resource_type: "task",
          resource_id: "task-1",
          resource_uri: "ui://tasks/detail/task-1",
          task_id: "task-1",
          title: "Task needs review",
          summary: "Review the deploy checklist",
        },
      },
    });
  });

  it("merges upload metadata with forward metadata", () => {
    expect(
      buildChatInputSendOptions({
        mentionedAgentIds: [],
        uploadMetadata: {
          attachments: [{ id: "attachment-1", filename: "notes.md" }],
        },
        forwardingCard: {
          cardId: "card-context-1",
          title: "Context artifact",
          resourceType: "context",
          contextKey: "upload:notes.md",
          attachments: [{ id: "attachment-1", filename: "notes.md" }],
        },
      }),
    ).toEqual({
      mentionedAgentIds: [],
      metadata: {
        attachments: [{ id: "attachment-1", filename: "notes.md" }],
        forward: {
          intent: "share",
          source_card_id: "card-context-1",
          resource_type: "context",
          context_key: "upload:notes.md",
          title: "Context artifact",
          attachments: [{ id: "attachment-1", filename: "notes.md" }],
        },
      },
    });
  });
});
