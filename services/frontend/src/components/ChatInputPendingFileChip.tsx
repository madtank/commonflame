/**
 * PendingFileChip — staged file indicator shown in ChatInput above the textarea.
 *
 * Handles two kinds:
 *   - image: thumbnail preview (existing behaviour, preserved)
 *   - file:  filename + size chip (new: PDFs, code, docs, etc.)
 *
 * Both kinds show a progress bar during upload and an error state on failure.
 *
 * Part of the file attachment widget (fix-file-attachment-widget).
 * Applied to: src/components/ChatInput.tsx
 */

import { X, FileText, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export type PendingFile =
  | { kind: "image"; file: File; preview: string }
  | { kind: "file"; file: File; preview: null };

export type UploadProgress = {
  phase: "idle" | "uploading" | "done" | "error";
  percent: number;
  errorMsg?: string;
};

interface PendingFileChipProps {
  pendingFile: PendingFile;
  uploadProgress: UploadProgress;
  onClear: () => void;
}

export function PendingFileChip({
  pendingFile,
  uploadProgress,
  onClear,
}: PendingFileChipProps) {
  const isUploading = uploadProgress.phase === "uploading";
  const isError = uploadProgress.phase === "error";

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {pendingFile.kind === "image" ? (
        <div className="relative">
          <img
            src={pendingFile.preview}
            alt="Pending upload"
            className="h-16 w-16 rounded-md object-cover"
          />
          {!isUploading && (
            <button
              type="button"
              onClick={onClear}
              className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-slate-800 text-slate-300 hover:bg-slate-700"
              aria-label="Remove image"
            >
              <X className="h-2.5 w-2.5" />
            </button>
          )}
        </div>
      ) : (
        <div
          className={cn(
            "flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-xs text-slate-300",
            isError && "border-red-400/30 text-red-300",
          )}
        >
          <FileText className="h-3.5 w-3.5 shrink-0 text-slate-400" />
          <span className="max-w-[160px] truncate">{pendingFile.file.name}</span>
          <span className="text-slate-500">
            {(pendingFile.file.size / 1024).toFixed(0)}KB
          </span>
          {!isUploading && (
            <button
              type="button"
              onClick={onClear}
              className="ml-1 text-slate-500 hover:text-slate-300"
              aria-label="Remove file"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      )}

      {isUploading && (
        <div className="flex items-center gap-2">
          <Loader2 className="h-3 w-3 animate-spin text-cyan-400" />
          <div className="h-1 w-20 overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full rounded-full bg-cyan-400 transition-all duration-200"
              style={{ width: `${uploadProgress.percent}%` }}
            />
          </div>
          <span className="text-xs text-slate-500">{uploadProgress.percent}%</span>
        </div>
      )}

      {isError && (
        <span className="text-xs text-red-400">{uploadProgress.errorMsg}</span>
      )}
    </div>
  );
}
