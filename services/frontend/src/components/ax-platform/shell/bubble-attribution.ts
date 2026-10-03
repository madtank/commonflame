export type BubbleAttribution = "self" | "peer" | "agent";

function normalize(value: string | null | undefined): string {
  if (typeof value !== "string") return "";
  return value.replace(/^@/, "").trim().toLowerCase();
}

export function classifyBubbleAttribution(
  entry: { role: "user" | "agent"; fromHandle?: string | null },
  viewerHandle: string | null | undefined,
): BubbleAttribution {
  if (entry.role === "agent") return "agent";
  const viewer = normalize(viewerHandle);
  const author = normalize(entry.fromHandle);
  if (!viewer || !author) return "self";
  return viewer === author ? "self" : "peer";
}

export function getBubbleTestId(attribution: BubbleAttribution): string {
  if (attribution === "self") return "ax-user-bubble";
  if (attribution === "peer") return "ax-peer-bubble";
  return "ax-agent-bubble";
}

export function getBubbleClassName(
  attribution: BubbleAttribution,
  isDarkMode: boolean,
): string {
  if (attribution === "self") {
    return isDarkMode
      ? "ml-auto max-w-2xl bg-[linear-gradient(135deg,#1d4ed8_0%,#2563eb_45%,#38bdf8_100%)] text-white"
      : "ml-auto max-w-2xl border border-blue-200/80 bg-blue-50 text-slate-900 shadow-[0_18px_40px_-28px_rgba(59,130,246,0.18)]";
  }
  if (attribution === "peer") {
    return isDarkMode
      ? "border border-slate-400/25 bg-slate-700/35 text-slate-100"
      : "border border-slate-300/80 bg-slate-100/80 text-slate-900 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.16)]";
  }
  return isDarkMode
    ? "border border-white/10 bg-white/[0.05] text-white"
    : "border border-slate-200 bg-white text-slate-900 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.16)]";
}
