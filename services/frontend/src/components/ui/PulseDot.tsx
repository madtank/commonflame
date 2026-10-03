import { cn } from "@/lib/utils";

interface PulseDotProps {
  active: boolean;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function PulseDot({ active, size = "sm", className }: PulseDotProps) {
  if (!active) return null;

  const sizeClasses = {
    sm: "w-2 h-2",
    md: "w-3 h-3",
    lg: "w-4 h-4",
  };

  return (
    <span className={cn("relative inline-flex", className)}>
      <span
        className={cn(
          "animate-ping absolute inline-flex rounded-full bg-green-400 opacity-75",
          sizeClasses[size]
        )}
      />
      <span
        className={cn(
          "relative inline-flex rounded-full bg-green-500",
          sizeClasses[size]
        )}
      />
    </span>
  );
}
