import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  parseReasoningContent,
  ReasoningContent,
  renderMarkdownContent,
} from "./MessageContent";

describe("parseReasoningContent", () => {
  it("splits reasoning and answer on paragraph break", () => {
    const parsed = parseReasoningContent(
      "Reasoning: Evaluate options first.\n\nShip the fix now.",
    );

    expect(parsed.reasoning).toBe("Evaluate options first.");
    expect(parsed.answer).toBe("Ship the fix now.");
  });

  it("splits inline reasoning when answer starts with 'Done —'", () => {
    const parsed = parseReasoningContent(
      "Reasoning: _**Clarifying**_ Done — I updated the docs.",
    );

    expect(parsed.reasoning).toContain("_**Clarifying**_");
    expect(parsed.answer).toBe("Done — I updated the docs.");
  });

  it("splits italic-wrapped reasoning in no-separator form", () => {
    const parsed = parseReasoningContent(
      "Reasoning: _**Audit path**_Done — deploy proof is attached.",
    );

    expect(parsed.reasoning).toContain("**Audit path**");
    expect(parsed.answer).toBe("Done — deploy proof is attached.");
  });

  it("treats italic-only no-separator payload as reasoning-only", () => {
    const parsed = parseReasoningContent("Reasoning: _internal trace only_");

    expect(parsed.reasoning).toBe("internal trace only");
    expect(parsed.answer).toBe("");
  });

  it("treats no-separator plain payload as answer-only", () => {
    const parsed = parseReasoningContent(
      "Reasoning: deploy path validated and complete.",
    );

    expect(parsed.reasoning).toBeNull();
    expect(parsed.answer).toBe("deploy path validated and complete.");
  });

  it("returns plain answer when no reasoning prefix exists", () => {
    const parsed = parseReasoningContent("No reasoning block here.");
    expect(parsed.reasoning).toBeNull();
    expect(parsed.answer).toBe("No reasoning block here.");
  });
});

describe("ReasoningContent", () => {
  const content = "Reasoning: check assumptions.\n\nFinal answer first.";

  it("collapses reasoning by default when requested", () => {
    render(<ReasoningContent text={content} collapseByDefault />);

    expect(screen.getByText("Show reasoning")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-answer")).toHaveTextContent(
      "Final answer first.",
    );
    expect(screen.queryByTestId("reasoning-content")).not.toBeInTheDocument();
  });

  it("toggles reasoning visibility", () => {
    render(<ReasoningContent text={content} collapseByDefault />);

    fireEvent.click(screen.getByText("Show reasoning"));
    expect(screen.getByText("Hide reasoning")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-content")).toHaveTextContent(
      "check assumptions.",
    );

    fireEvent.click(screen.getByText("Hide reasoning"));
    expect(screen.getByText("Show reasoning")).toBeInTheDocument();
    expect(screen.queryByTestId("reasoning-content")).not.toBeInTheDocument();
  });

  it("auto-collapses when autoCollapse flips to true", () => {
    const { rerender } = render(
      <ReasoningContent
        text={content}
        collapseByDefault={false}
        autoCollapse={false}
      />,
    );

    expect(screen.getByText("Hide reasoning")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-content")).toBeInTheDocument();

    rerender(
      <ReasoningContent
        text={content}
        collapseByDefault={false}
        autoCollapse={true}
      />,
    );

    expect(screen.getByText("Show reasoning")).toBeInTheDocument();
    expect(screen.queryByTestId("reasoning-content")).not.toBeInTheDocument();
  });

  it("preserves answer text with sanitizeSegments on inline reasoning", () => {
    render(
      <ReasoningContent
        text="Reasoning: _**Audit path**_ Done — deploy proof is attached."
        collapseByDefault
        sanitizeSegments
      />,
    );

    expect(screen.getByTestId("reasoning-answer")).toHaveTextContent(
      "Done — deploy proof is attached.",
    );
  });

  it("wraps long markdown content without enabling horizontal scroll on the outer message body", () => {
    const longUrl = "https://example.com/" + "segment".repeat(30);
    const { container } = render(
      <>{renderMarkdownContent(`Visit ${longUrl}`)}</>,
    );

    const markdownRoot = container.querySelector(".prose");
    const link = container.querySelector("a");

    expect(markdownRoot).toHaveClass("overflow-x-hidden");
    expect(markdownRoot).toHaveClass("overflow-wrap-anywhere");
    expect(markdownRoot?.className).not.toContain("overflow-x-auto");
    expect(link).toHaveClass("break-all");
  });

  it("keeps code blocks and tables inside the stream width on mobile", () => {
    const { container } = render(
      <>
        {renderMarkdownContent(
          [
            "```",
            "const veryLongToken = " + "x".repeat(80),
            "```",
            "",
            "| Headline Column | Another Column |",
            "| --- | --- |",
            "| " + "wide-cell-".repeat(8) + " | value |",
          ].join("\n"),
        )}
      </>,
    );

    const pre = container.querySelector("pre");
    const tableWrapper = container.querySelector(
      '[aria-label="Markdown table"]',
    );
    const table = container.querySelector("table");
    const headerCell = container.querySelector("th");
    const bodyCell = container.querySelector("td");

    expect(pre).toHaveClass("overflow-x-hidden");
    expect(pre).toHaveClass("break-all");
    expect(pre?.className).not.toContain("overflow-x-auto");
    expect(tableWrapper).toHaveClass("overflow-x-hidden");
    expect(table).toHaveClass("w-full");
    expect(table).toHaveClass("table-fixed");
    expect(headerCell).toHaveClass("break-words");
    expect(bodyCell).toHaveClass("break-words");
  });

  it("hides reasoning panel when showReasoningPanel is false", () => {
    render(
      <ReasoningContent
        text={content}
        collapseByDefault
        showReasoningPanel={false}
      />,
    );

    expect(screen.queryByText("Show reasoning")).not.toBeInTheDocument();
    expect(screen.queryByText("Hide reasoning")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reasoning-content")).not.toBeInTheDocument();
    expect(screen.getByText("Final answer first.")).toBeInTheDocument();
  });
});
