import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  PendingFileChip,
  type PendingFile,
  type UploadProgress,
} from "./ChatInputPendingFileChip";

describe("ChatInputPendingFileChip", () => {
  it("renders non-image attachments with filename, size, and remove affordance", () => {
    const onClear = vi.fn();
    const pendingFile: PendingFile = {
      kind: "file",
      file: new File(["test"], "spec.pdf", { type: "application/pdf" }),
      preview: null,
    };
    const uploadProgress: UploadProgress = {
      phase: "idle",
      percent: 0,
    };

    render(
      <PendingFileChip
        pendingFile={pendingFile}
        uploadProgress={uploadProgress}
        onClear={onClear}
      />,
    );

    expect(screen.getByText("spec.pdf")).toBeInTheDocument();
    expect(screen.getByText("0KB")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /remove file/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("renders upload progress and errors for staged attachments", () => {
    const pendingFile: PendingFile = {
      kind: "file",
      file: new File(["test"], "data.json", { type: "application/json" }),
      preview: null,
    };

    const { rerender } = render(
      <PendingFileChip
        pendingFile={pendingFile}
        uploadProgress={{ phase: "uploading", percent: 42 }}
        onClear={() => undefined}
      />,
    );

    expect(screen.getByText("42%")).toBeInTheDocument();

    rerender(
      <PendingFileChip
        pendingFile={pendingFile}
        uploadProgress={{
          phase: "error",
          percent: 0,
          errorMsg: "Upload failed",
        }}
        onClear={() => undefined}
      />,
    );

    expect(screen.getByText("Upload failed")).toBeInTheDocument();
  });
});
