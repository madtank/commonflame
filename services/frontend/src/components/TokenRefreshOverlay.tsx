import React from 'react';
import { Loader2 } from 'lucide-react';

interface TokenRefreshOverlayProps {
  isRefreshing: boolean;
  message?: string;
}

export function TokenRefreshOverlay({ isRefreshing, message = "Refreshing session..." }: TokenRefreshOverlayProps) {
  if (!isRefreshing) return null;

  return (
    <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50 flex items-center justify-center">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl p-6 flex items-center space-x-3">
        <Loader2 className="h-5 w-5 animate-spin text-indigo-600 dark:text-indigo-400" />
        <span className="text-gray-700 dark:text-gray-300 font-medium">
          {message}
        </span>
      </div>
    </div>
  );
}
