import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExternalMarkdownLink } from "./external-markdown-link";

describe("ExternalMarkdownLink", () => {
  it("opens safe links in a new tab without opener access", () => {
    render(
      <ExternalMarkdownLink href="https://example.com/report">
        Report
      </ExternalMarkdownLink>,
    );

    const link = screen.getByRole("link", { name: "Report" });
    expect(link).toHaveAttribute("href", "https://example.com/report");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not render dangerous href protocols as links", () => {
    render(
      <ExternalMarkdownLink href="javascript:alert(1)">
        Bad
      </ExternalMarkdownLink>,
    );

    expect(screen.queryByRole("link", { name: "Bad" })).not.toBeInTheDocument();
    expect(screen.getByText("Bad")).toBeInTheDocument();
  });
});
