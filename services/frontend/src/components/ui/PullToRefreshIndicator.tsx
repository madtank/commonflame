/**
 * PullToRefreshIndicator.tsx
 *
 * Visual feedback for pull-to-refresh gesture.
 * Shows an arrow that rotates into a spinner when threshold is reached.
 */
import { RefreshCw } from "lucide-react";

interface Props {
  pullDistance: number;
  isRefreshing: boolean;
  isPastThreshold: boolean;
  threshold?: number;
}

export function PullToRefreshIndicator({
  pullDistance,
  isRefreshing,
  isPastThreshold,
  threshold = 80,
}: Props) {
  if (pullDistance === 0 && !isRefreshing) return null;

  const progress = Math.min(1, pullDistance / threshold);
  const rotation = progress * 180;

  return (
    <div
      className="flex items-center justify-center overflow-hidden transition-[height] duration-200 ease-out"
      style={{ height: isRefreshing ? 40 : pullDistance * 0.6 }}
    >
      <div
        className={`flex items-center justify-center rounded-full w-8 h-8 ${
          isPastThreshold || isRefreshing
            ? "bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400"
            : "bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500"
        } transition-colors`}
      >
        <RefreshCw
          className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`}
          style={
            isRefreshing ? undefined : { transform: `rotate(${rotation}deg)` }
          }
        />
      </div>
    </div>
  );
}
