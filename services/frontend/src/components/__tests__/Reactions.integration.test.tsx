/**
 * Integration tests for emoji reactions system
 * Tests the complete flow from UI interaction to API calls
 */

import { render, screen, fireEvent, waitFor } from "@/test/utils";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { MessageList } from "../MessageList";
import { api } from "@/lib/api-clean";
import "@testing-library/jest-dom";

// Mock the API (Vitest)
vi.mock("@/lib/api-clean", () => ({
  api: {
    postMessage: vi.fn(),
    getUserMessages: vi.fn(),
  },
}));

// Mock toast notifications
vi.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({
    toast: vi.fn(),
  }),
}));

describe("Emoji Reactions Integration Tests", () => {
  const mockPosts = [
    {
      id: 1,
      content: "Test message",
      uploaded_at: "2024-01-01T00:00:00Z",
      username: "testuser",
      channel: "main",
    },
    {
      id: 2,
      content: "👍",
      uploaded_at: "2024-01-01T00:01:00Z",
      username: "reactor",
      parent_id: "1", // This is a reaction to message 1
      channel: "main",
    },
  ];

  const defaultProps = {
    posts: mockPosts,
    isLoading: false,
    timeRange: "all",
    displayLimit: 100,
    acknowledgedBlocked: new Set<number>(),
    setAcknowledgedBlocked: vi.fn(),
    onMessageRead: vi.fn(),
    onPostExpand: vi.fn(),
    isMessageRead: vi.fn(() => false),
    isPostExpanded: vi.fn(() => false),
    onHashtagClick: vi.fn(),
    onAgentClick: vi.fn(),
    onReplyToPost: vi.fn(),
    messagesContainerRef: { current: null },
    messagesEndRef: { current: null },
    onScroll: vi.fn(),
    onScrollToBottom: vi.fn(),
    totalAvailable: 2,
    messagesShowing: 2,
    hasOlderMessages: false,
    onLoadMore: vi.fn(),
    pageSize: 100,
    viewerUsername: "testuser",
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Parent ID Association", () => {
    it("should send parent_id when clicking a reaction button", async () => {
      const { container } = render(<MessageList {...defaultProps} />);

      // Find the first message's reaction button (thumbs up)
      const reactionButtons = container.querySelectorAll(
        '[aria-label="Thumbs up"]',
      );
      expect(reactionButtons.length).toBeGreaterThan(0);

      // Click the reaction button
      fireEvent.click(reactionButtons[0]);

      // Wait for API call
      await waitFor(() => {
        expect(api.postMessage).toHaveBeenCalledWith(
          "👍",
          expect.objectContaining({
            parentId: 1, // Should include parent ID
          }),
        );
      });
    });

    it("should not render reaction messages as standalone posts", () => {
      const { container } = render(<MessageList {...defaultProps} />);

      // The reaction (message id 2) should not be visible as a standalone message
      const messages = screen.getAllByText(/Test message/);
      expect(messages).toHaveLength(1); // Only the parent message should be visible

      // Check that only 1 message row exists (not 2) - reaction should be filtered
      const messageRows = container.querySelectorAll("[data-post-id]");
      expect(messageRows).toHaveLength(1);
      expect(messageRows[0]).toHaveAttribute("data-post-id", "1");
    });
  });

  describe("Reaction Deduplication", () => {
    it("should prevent duplicate reactions from same user", async () => {
      const postsWithUserReaction = [
        ...mockPosts,
        {
          id: 3,
          content: "👍",
          uploaded_at: "2024-01-01T00:02:00Z",
          username: "testuser", // Same as viewer
          parent_id: "1",
          channel: "main",
        },
      ];

      const { container } = render(
        <MessageList {...defaultProps} posts={postsWithUserReaction} />,
      );

      // Find the reaction button
      const reactionButton = container.querySelector(
        '[aria-label="Thumbs up"]',
      );

      // The button should show as already selected
      expect(reactionButton).toHaveAttribute("aria-pressed", "true");

      // Clicking it again should not send another reaction
      fireEvent.click(reactionButton!);

      await waitFor(() => {
        // Should log that user already reacted
        expect(api.postMessage).not.toHaveBeenCalled();
      });
    });
  });

  describe("Multiple Reaction Types", () => {
    it("should open emoji picker when clicking add reaction plus button", async () => {
      const { container } = render(<MessageList {...defaultProps} />);

      const addReactionButton = container.querySelector(
        '[aria-label="Add reaction"]',
      );
      expect(addReactionButton).toBeTruthy();

      fireEvent.click(addReactionButton!);

      await waitFor(() => {
        expect(addReactionButton).toHaveAttribute("aria-expanded", "true");
        expect(
          screen.getByRole("dialog", { name: "Emoji picker" }),
        ).toBeInTheDocument();
      });
    });

    it("should allow multiple different reactions from same user", async () => {
      const { container } = render(<MessageList {...defaultProps} />);

      // Click different reaction types (using updated emoji set)
      const thumbsUpButton = container.querySelector(
        '[aria-label="Thumbs up"]',
      );
      const fireButton = container.querySelector('[aria-label="Fire"]');
      const rocketButton = container.querySelector('[aria-label="Rocket"]');

      // Click each button and wait for the API call
      fireEvent.click(thumbsUpButton!);
      await waitFor(() => {
        expect(api.postMessage).toHaveBeenCalledWith(
          "👍",
          expect.objectContaining({ parentId: 1 }),
        );
      });

      fireEvent.click(fireButton!);
      await waitFor(() => {
        expect(api.postMessage).toHaveBeenCalledWith(
          "🔥",
          expect.objectContaining({ parentId: 1 }),
        );
      });

      fireEvent.click(rocketButton!);
      await waitFor(() => {
        expect(api.postMessage).toHaveBeenCalledWith(
          "🚀",
          expect.objectContaining({ parentId: 1 }),
        );
      });

      // Verify total calls
      expect(api.postMessage).toHaveBeenCalledTimes(3);
    });
  });

  describe("Reply vs Reaction Distinction", () => {
    it.skip("should show text replies with reply header", () => {
      const postsWithReply = [
        mockPosts[0],
        {
          id: 3,
          content: "This is a text reply",
          uploaded_at: "2024-01-01T00:02:00Z",
          username: "replier",
          parent_id: 1, // Use numeric ID to match the parent post's numeric ID
          channel: "main",
        },
      ];

      render(<MessageList {...defaultProps} posts={postsWithReply} />);

      // Text reply should be visible
      expect(screen.getByText("This is a text reply")).toBeInTheDocument();

      // Should show reply indicator - use more flexible matcher since text is split across elements
      expect(
        screen.getByText((content, element) => {
          return (
            element?.textContent?.includes("↳ Reply to @testuser") ?? false
          );
        }),
      ).toBeInTheDocument();
    });

    it("should hide emoji reactions from message list", () => {
      const postsWithReactions = [
        mockPosts[0],
        { ...mockPosts[1], content: "👍" },
        {
          id: 3,
          content: "👎",
          username: "user2",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:02:00Z",
          channel: "main",
        },
        {
          id: 4,
          content: "🔥",
          username: "user3",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:03:00Z",
          channel: "main",
        },
      ];

      const { container } = render(
        <MessageList {...defaultProps} posts={postsWithReactions} />,
      );

      // Only the parent message should be visible as a message row
      const visibleMessages = screen.getAllByText(/Test message/);
      expect(visibleMessages).toHaveLength(1);

      // Emoji reactions should not be rendered as message rows
      const messageRows = container.querySelectorAll("[data-post-id]");
      expect(messageRows).toHaveLength(1); // Only the parent message
      expect(messageRows[0]).toHaveAttribute("data-post-id", "1");
    });

    it("should hide command reactions (!kudos, !flag, etc)", () => {
      const postsWithCommands = [
        mockPosts[0],
        {
          id: 3,
          content: "!kudos",
          username: "user2",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:02:00Z",
          channel: "main",
        },
        {
          id: 4,
          content: "!flag",
          username: "user3",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:03:00Z",
          channel: "main",
        },
      ];

      render(<MessageList {...defaultProps} posts={postsWithCommands} />);

      // Command reactions should not be visible
      expect(screen.queryByText("!kudos")).toBeNull();
      expect(screen.queryByText("!flag")).toBeNull();
    });
  });

  describe("Reaction Counts", () => {
    it("should aggregate and display reaction counts correctly", () => {
      const postsWithMultipleReactions = [
        mockPosts[0],
        {
          id: 2,
          content: "👍",
          username: "user1",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:01:00Z",
          channel: "main",
        },
        {
          id: 3,
          content: "👍",
          username: "user2",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:02:00Z",
          channel: "main",
        },
        {
          id: 4,
          content: "👍",
          username: "user3",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:03:00Z",
          channel: "main",
        },
      ];

      const { container } = render(
        <MessageList {...defaultProps} posts={postsWithMultipleReactions} />,
      );

      // Should show count of 3 for thumbs up
      const thumbsUpButton = container.querySelector(
        '[aria-label="Thumbs up"]',
      );
      expect(thumbsUpButton).toBeTruthy();
      expect(thumbsUpButton?.textContent).toContain("3");
    });

    it("should deduplicate reactions from same user", () => {
      const postsWithDuplicates = [
        mockPosts[0],
        {
          id: 2,
          content: "👍",
          username: "user1",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:01:00Z",
          channel: "main",
        },
        {
          id: 3,
          content: "👍",
          username: "user1",
          parent_id: "1",
          uploaded_at: "2024-01-01T00:02:00Z",
          channel: "main",
        }, // Duplicate
      ];

      const { container } = render(
        <MessageList {...defaultProps} posts={postsWithDuplicates} />,
      );

      // Should only count once per user
      const thumbsUpButton = container.querySelector(
        '[aria-label="Thumbs up"]',
      );
      expect(thumbsUpButton).toBeTruthy();
      expect(thumbsUpButton?.textContent).toContain("1");
    });
  });

  describe("Error Handling", () => {
    it("should handle API failures gracefully", async () => {
      vi.mocked(api.postMessage).mockRejectedValueOnce(new Error("API Error"));

      const { container } = render(<MessageList {...defaultProps} />);

      const reactionButton = container.querySelector(
        '[aria-label="Thumbs up"]',
      );
      fireEvent.click(reactionButton!);

      await waitFor(() => {
        // Should have attempted to call API
        expect(api.postMessage).toHaveBeenCalled();
      });

      // UI should remain functional
      expect(reactionButton).toBeInTheDocument();
    });
  });
});
