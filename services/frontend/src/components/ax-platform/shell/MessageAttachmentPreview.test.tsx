import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageAttachmentPreview } from "./MessageAttachmentPreview";

describe("MessageAttachmentPreview", () => {
  it("renders audio attachment metadata as an inline player", () => {
    render(
      <MessageAttachmentPreview
        attachment={{
          name: "1e6b5c58680d4bd58c299e62db2c802d.ogg",
          url: "/api/v1/uploads/files/1e6b5c58680d4bd58c299e62db2c802d.ogg",
          contentType: "audio/ogg",
          sizeBytes: 72184,
        }}
        userTone={false}
      />,
    );

    expect(screen.getByTestId("message-audio")).toBeInTheDocument();
    expect(
      screen.getByText("1e6b5c58680d4bd58c299e62db2c802d.ogg"),
    ).toBeInTheDocument();
  });

  it("renders video attachment metadata as an inline player", () => {
    render(
      <MessageAttachmentPreview
        attachment={{
          name: "demo.mp4",
          url: "https://cdn.example.com/demo.mp4",
          contentType: "video/mp4",
          sizeBytes: 2048,
        }}
        userTone={false}
      />,
    );

    expect(screen.getByTestId("message-video")).toBeInTheDocument();
    expect(screen.getByText("demo.mp4")).toBeInTheDocument();
  });

  it("keeps non-media attachments as openable file pills", () => {
    render(
      <MessageAttachmentPreview
        attachment={{
          name: "notes.pdf",
          url: "/api/v1/uploads/files/notes.pdf",
          contentType: "application/pdf",
          sizeBytes: 2048,
        }}
        userTone={false}
      />,
    );

    expect(screen.queryByTestId("message-audio")).not.toBeInTheDocument();
    expect(screen.queryByTestId("message-video")).not.toBeInTheDocument();
    expect(screen.getByText("notes.pdf")).toBeInTheDocument();
    expect(screen.getByText("Attachment · 2 KB")).toBeInTheDocument();
  });

  it("constrains long attachment cards to the mobile viewport", () => {
    render(
      <MessageAttachmentPreview
        attachment={{
          name: "jacob-mobile-overflow-screenshot-with-a-very-long-filename.pdf",
          url: "/api/v1/uploads/files/jacob-mobile-overflow.pdf",
          contentType: "application/pdf",
          sizeBytes: 2048,
        }}
        userTone={false}
      />,
    );

    const filename = screen.getByText(
      "jacob-mobile-overflow-screenshot-with-a-very-long-filename.pdf",
    );
    const card = filename.closest("span.inline-flex");
    const wrapper = filename.closest("a,button");

    expect(card).toHaveClass("max-w-full");
    expect(card).toHaveClass("w-full");
    expect(wrapper).toHaveClass("max-w-full");
  });

  it("does not render local runtime paths as playable media", () => {
    render(
      <MessageAttachmentPreview
        attachment={{
          name: "local-runtime.wav",
          url: "/tmp/supertonic3_audio/local-runtime.wav",
          contentType: "audio/wav",
          sizeBytes: 2048,
        }}
        userTone={false}
      />,
    );

    expect(screen.queryByTestId("message-audio")).not.toBeInTheDocument();
    expect(screen.getByText("local-runtime.wav")).toBeInTheDocument();
    expect(screen.getByText("Attachment · 2 KB")).toBeInTheDocument();
  });
});
