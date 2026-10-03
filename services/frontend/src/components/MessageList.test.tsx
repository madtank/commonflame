import { render, screen } from "@/test/utils";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { MessageList } from "./MessageList";

const baseProps = {
  posts: [
    {
      id: 1,
      content: "Hello world",
      uploaded_at: "2025-08-07T11:59:00Z",
      username: "agent1",
    },
  ],
  isLoading: false,
  timeRange: "all",
  displayLimit: 0,
  acknowledgedBlocked: new Set<number>(),
  setAcknowledgedBlocked: () => {},
  onMessageRead: () => {},
  onPostExpand: () => {},
  isMessageRead: () => true,
  isPostExpanded: () => false,
  onHashtagClick: () => {},
  onAgentClick: () => {},
  onReplyToPost: () => {},
  messagesContainerRef: { current: null },
  messagesEndRef: { current: null },
  onScroll: () => {},
  totalAvailable: 1,
  messagesShowing: 1,
  hasOlderMessages: false,
  onLoadMore: () => {},
  pageSize: 50,
};
const oldTimestamp = "2025-08-07T10:00:00Z";

describe("MessageList", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2025-08-07T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders relative timestamp", () => {
    render(<MessageList {...baseProps} />);
    // The relative time is produced by date-fns; we just check that a line exists
    expect(screen.getByText(/ago|minute|second/i)).toBeInTheDocument();
  });

  it("dedupes duplicate post ids by keeping the newest", () => {
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            id: 42,
            content: "Older version",
            uploaded_at: "2025-08-07T11:00:00Z",
            username: "agent1",
          },
          {
            id: 42,
            content: "Latest version",
            uploaded_at: "2025-08-07T11:30:00Z",
            username: "agent1",
          },
        ]}
      />,
    );

    expect(screen.getByText("Latest version")).toBeInTheDocument();
    expect(screen.queryByText("Older version")).not.toBeInTheDocument();
  });

  it("adds iphone-safe classes/padding on iPhone UA", () => {
    const originalUA = navigator.userAgent;
    // Ensure touch event flag exists so the first detection branch is true
    (document as any).ontouchend = null;
    Object.defineProperty(window, "navigator", {
      value: { userAgent: "iPhone; CPU iPhone OS 17_0 like Mac OS X" },
      configurable: true,
    });

    render(<MessageList {...baseProps} />);
    const container = document.querySelector(
      ".iphone-scroll",
    ) as HTMLElement | null;
    expect(container).not.toBeNull();
    // iPhone class should be present, but padding is now handled by spacer div
    expect(container?.classList.contains("iphone-scroll")).toBe(true);
    expect(container?.style.paddingBottom).toBe("");

    // Clean up
    Object.defineProperty(window, "navigator", {
      value: { userAgent: originalUA },
    });
  });

  it("does NOT add iphone padding on desktop UA", () => {
    const originalUA = navigator.userAgent;
    Object.defineProperty(window, "navigator", {
      value: {
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Chrome/120.0.0.0 Safari/537.36",
      },
      configurable: true,
    });
    render(<MessageList {...baseProps} />);
    const container = document.querySelector(
      ".messages-scroll",
    ) as HTMLElement | null;
    expect(container).not.toBeNull();
    // Should not have iphone-scroll class
    expect(container?.classList.contains("iphone-scroll")).toBe(false);
    // Should not have inline paddingBottom (desktop uses spacer only)
    expect(container?.style.paddingBottom).toBe("");
    Object.defineProperty(window, "navigator", {
      value: { userAgent: originalUA },
    });
  });

  it("renders pause messages with countdowns when message_type is agent_pause", () => {
    const pauseExpiresAt = new Date("2025-08-07T12:00:30Z").toISOString();
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            ...baseProps.posts[0],
            content: "💤 Taking a break",
            message_type: "agent_pause",
            pause_duration: 30,
            pause_expires_at: pauseExpiresAt,
          },
        ]}
      />,
    );

    expect(screen.getByText("Taking a break")).toBeInTheDocument();
    expect(screen.getByText("30s")).toBeInTheDocument();
  });

  it("prefers metadata agent_name over username for pause messages", () => {
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            ...baseProps.posts[0],
            username: "operator_user",
            message_type: "agent_pause",
            metadata: {
              ...((baseProps.posts[0] as any).metadata || {}),
              agent_name: "Cortex",
              reason_text: "is sipping matcha",
            },
            pause_duration: 15,
            pause_expires_at: new Date(Date.now() + 15000).toISOString(),
          },
        ]}
      />,
    );

    expect(screen.getByText("Cortex is sipping matcha")).toBeInTheDocument();
    expect(
      screen.queryByText(/operator_user is sipping matcha/i),
    ).not.toBeInTheDocument();
  });

  it("renders reasoning-prefixed single-line payloads without blanking content (regression)", () => {
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            ...baseProps.posts[0],
            id: 99,
            content: "Reasoning: deploy path validated and complete.",
            author_type: "agent",
          },
        ]}
      />,
    );

    // Timeline path should hide reasoning panel, but never blank content.
    expect(
      screen.queryByRole("button", { name: /show reasoning/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Reasoning: deploy path validated and complete."),
    ).toBeInTheDocument();
  });

  it("renders rich gateway activity details inside a highlighted activity box", () => {
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            ...baseProps.posts[0],
            id: 101,
            username: "human_user",
            mentions: ["@frontend_sentinel"],
          },
        ]}
        agentActivityByPost={
          new Map([
            [
              "101",
              {
                tool_name: "python",
                status: "tool_call",
                timestamp: Date.now(),
                command: "python scripts/check_gateway_activity.py",
              },
            ],
          ])
        }
      />,
    );

    expect(screen.getByText("Using python")).toBeInTheDocument();
    expect(
      screen.getByText("python scripts/check_gateway_activity.py"),
    ).toBeInTheDocument();
    const activityBox = document.querySelector(
      '[data-agent-activity="true"]',
    ) as HTMLElement | null;
    expect(activityBox).not.toBeNull();
    expect(activityBox?.className).toContain("border-violet");
    expect(activityBox?.className).toContain("bg-gradient-to-r");
  });

  it("does not crash when gateway activity details are already serialized from object payloads", () => {
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            ...baseProps.posts[0],
            id: 102,
            username: "human_user",
            mentions: ["@frontend_sentinel"],
          },
        ]}
        agentActivityByPost={
          new Map([
            [
              "102",
              {
                tool_name: "tasks",
                status: "tool_call",
                timestamp: Date.now(),
                details: JSON.stringify({ action: "list", limit: 3 }),
              },
            ],
          ])
        }
      />,
    );

    expect(screen.getByText("Using tasks")).toBeInTheDocument();
    expect(screen.getByText('{"action":"list","limit":3}')).toBeInTheDocument();
  });

  it("wraps long mobile metadata within message bounds (regression)", () => {
    render(
      <MessageList
        {...baseProps}
        posts={[
          {
            ...baseProps.posts[0],
            id: 100,
            username: "frontend_sentinel",
            author_type: "agent",
            mentions: [
              "@frontend_sentinel_super_long_agent_handle",
              "@backend_sentinel_super_long_agent_handle",
              "@mcp_sentinel_super_long_agent_handle",
            ],
          },
        ]}
        pendingCloudAgentPosts={new Set([100])}
      />,
    );

    const agentBadgeRow = screen.getByText("AGENT").parentElement;
    expect(agentBadgeRow?.className).toContain("min-w-0");
    expect(agentBadgeRow?.className).toContain("flex-1");
    expect(agentBadgeRow?.className).toContain("flex-wrap");

    const activityChip = screen.getByText(
      /Processing to @frontend_sentinel_super_long_agent_handle/,
    ).parentElement;
    expect(activityChip?.className).toContain("max-w-full");
    expect(activityChip?.className).toContain("flex-wrap");

    const replyButton = screen.getByRole("button", { name: "Reply" });
    expect(replyButton.parentElement?.className).toContain("flex-wrap");
  });

  describe("Image display behavior", () => {
    it("always shows images even when condensed (short text + image)", () => {
      const postWithImage = {
        id: 2,
        content:
          "Here is a screenshot: ![screenshot](https://example.com/image.png)",
        uploaded_at: oldTimestamp,
        username: "agent2",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithImage]}
          isGlobalCondensedMode={true}
        />,
      );

      // Image should always be visible, never hidden
      const img = screen.getByRole("img", { name: "screenshot" });
      expect(img).toBeInTheDocument();
    });

    it("shows truncation controls for long text", () => {
      const longText = "A".repeat(400); // > 300 chars triggers condensing
      const postWithLongText = {
        id: 3,
        content: longText,
        uploaded_at: oldTimestamp,
        username: "agent3",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithLongText]}
          isGlobalCondensedMode={true}
        />,
      );

      // Should show truncation controls
      expect(screen.getByText("Show full message")).toBeInTheDocument();
    });

    it("displays AI summary badge when ai_summary exists", () => {
      const postWithSummary = {
        id: 4,
        content: "A".repeat(400),
        uploaded_at: oldTimestamp,
        username: "agent4",
        author_type: "agent",
        ai_summary: "This is a test summary",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithSummary]}
          isGlobalCondensedMode={true}
        />,
      );

      // Should show AI Summary badge
      expect(screen.getByText("AI Summary")).toBeInTheDocument();
      expect(screen.getByText(/"This is a test summary"/)).toBeInTheDocument();
    });

    it("does not show AI summary badge for truncated text without summary", () => {
      const postWithoutSummary = {
        id: 5,
        content: "A".repeat(400),
        uploaded_at: oldTimestamp,
        username: "agent5",
        author_type: "agent",
        // No ai_summary field
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithoutSummary]}
          isGlobalCondensedMode={true}
        />,
      );

      // Should NOT show AI Summary badge
      expect(screen.queryByText("AI Summary")).not.toBeInTheDocument();
      // Should show the "Generate AI Summary" button instead
      expect(screen.getByText("Generate AI Summary")).toBeInTheDocument();
    });

    it("shows images even when text is condensed in messages with long text + images", () => {
      const longTextWithImage =
        "A".repeat(400) +
        "\n\n![screenshot](https://example.com/screenshot.png)";
      const postWithTextAndImage = {
        id: 6,
        content: longTextWithImage,
        uploaded_at: oldTimestamp,
        username: "agent6",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithTextAndImage]}
          isGlobalCondensedMode={true}
        />,
      );

      // Image should ALWAYS be visible even in condensed mode
      const img = screen.getByRole("img", { name: "screenshot" });
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute("src", "https://example.com/screenshot.png");

      // Text should be condensed - show truncation controls
      expect(screen.getByText("Show full message")).toBeInTheDocument();

      // Should show char count + image indicator
      expect(screen.getByText(/chars \+ 1 image/)).toBeInTheDocument();
    });

    it("shows multiple images when text is condensed", () => {
      const textWithMultipleImages =
        "A".repeat(400) +
        "\n\n![img1](https://example.com/img1.png)" +
        "\n![img2](https://example.com/img2.png)";
      const postWithMultipleImages = {
        id: 7,
        content: textWithMultipleImages,
        uploaded_at: oldTimestamp,
        username: "agent7",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithMultipleImages]}
          isGlobalCondensedMode={true}
        />,
      );

      // Both images should be visible
      const img1 = screen.getByRole("img", { name: "img1" });
      const img2 = screen.getByRole("img", { name: "img2" });
      expect(img1).toBeInTheDocument();
      expect(img2).toBeInTheDocument();
      expect(img1).toHaveAttribute("src", "https://example.com/img1.png");
      expect(img2).toHaveAttribute("src", "https://example.com/img2.png");

      // Should indicate 2 images
      expect(screen.getByText(/\+ 2 images/)).toBeInTheDocument();
    });

    it("does not condense short text with images", () => {
      const shortTextWithImage =
        "Short text\n\n![screenshot](https://example.com/img.png)";
      const postWithShortTextAndImage = {
        id: 8,
        content: shortTextWithImage,
        uploaded_at: oldTimestamp,
        username: "agent8",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithShortTextAndImage]}
          isGlobalCondensedMode={true}
        />,
      );

      // Image should be visible
      const img = screen.getByRole("img", { name: "screenshot" });
      expect(img).toBeInTheDocument();

      // Should NOT show truncation controls (text is short)
      expect(screen.queryByText("Show full message")).not.toBeInTheDocument();
    });
  });

  describe("Audio and video rendering", () => {
    it("renders audio player for audio URLs", () => {
      const postWithAudio = {
        id: 9,
        content: "Listen here https://example.com/song.mp3",
        uploaded_at: oldTimestamp,
        username: "agent9",
        author_type: "agent",
      };

      render(<MessageList {...baseProps} posts={[postWithAudio]} />);

      expect(screen.getAllByTestId("message-audio").length).toBe(1);
    });

    it("renders video player for video URLs", () => {
      const postWithVideo = {
        id: 10,
        content: "Watch this https://example.com/clip.mp4",
        uploaded_at: oldTimestamp,
        username: "agent10",
        author_type: "agent",
      };

      render(<MessageList {...baseProps} posts={[postWithVideo]} />);

      expect(screen.getAllByTestId("message-video").length).toBe(1);
    });

    it("renders YouTube embed previews for YouTube links", () => {
      const postWithYouTube = {
        id: 11,
        content: "Check this out https://www.youtube.com/watch?v=qLk7xuJ7Ki8",
        uploaded_at: oldTimestamp,
        username: "agent11",
        author_type: "agent",
      };

      render(<MessageList {...baseProps} posts={[postWithYouTube]} />);

      expect(screen.getAllByTestId("message-youtube").length).toBe(1);
    });

    it("shows media players even when condensed", () => {
      const postWithMedia = {
        id: 12,
        content:
          "Short text https://example.com/song.mp3 https://example.com/clip.webm",
        uploaded_at: oldTimestamp,
        username: "agent12",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithMedia]}
          isGlobalCondensedMode={true}
        />,
      );

      expect(screen.getAllByTestId("message-audio").length).toBe(1);
      expect(screen.getAllByTestId("message-video").length).toBe(1);
    });

    it("limits media in condensed mode when over five items", () => {
      const audioUrls = [
        "https://example.com/track1.mp3",
        "https://example.com/track2.mp3",
        "https://example.com/track3.mp3",
        "https://example.com/track4.mp3",
        "https://example.com/track5.mp3",
        "https://example.com/track6.mp3",
      ];
      const postWithManyTracks = {
        id: 13,
        content: `Mix: ${audioUrls.join(" ")}`,
        uploaded_at: oldTimestamp,
        username: "agent13",
        author_type: "agent",
      };

      render(
        <MessageList
          {...baseProps}
          posts={[postWithManyTracks]}
          isGlobalCondensedMode={true}
        />,
      );

      expect(screen.getAllByTestId("message-audio").length).toBe(5);
      expect(
        screen.getByText("Showing 5 of 6 media items"),
      ).toBeInTheDocument();
    });

    it("ignores media URLs with disallowed protocols", () => {
      const postWithUnsafeMedia = {
        id: 14,
        content: "![bad](javascript:alert(1)) javascript:alert(1).mp3",
        uploaded_at: oldTimestamp,
        username: "agent14",
        author_type: "agent",
      };

      render(<MessageList {...baseProps} posts={[postWithUnsafeMedia]} />);

      expect(screen.queryByAltText("bad")).not.toBeInTheDocument();
      expect(screen.queryByTestId("message-audio")).not.toBeInTheDocument();
      expect(screen.queryByTestId("message-video")).not.toBeInTheDocument();
    });
  });
});
