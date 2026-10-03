import { AxPlatformShell } from "@/components/AxPlatformShell";
import { TooltipProvider } from "@/components/ui/tooltip";

export default function AxPlatformPage({
  spaceName,
  username,
  onLogout,
}: {
  spaceName?: string;
  username?: string | null;
  onLogout?: () => void;
}) {
  return (
    <TooltipProvider delayDuration={300}>
      <AxPlatformShell
        spaceName={spaceName}
        agentName="Waystation"
        username={username}
        onLogout={onLogout}
      />
    </TooltipProvider>
  );
}
