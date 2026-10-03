
import React from 'react';

export function SummarySkeleton() {
    return (
        <div className="w-full space-y-1.5 animate-pulse py-1">
            <div className="h-3 bg-indigo-100 dark:bg-indigo-900/30 rounded w-11/12"></div>
            <div className="h-3 bg-indigo-100 dark:bg-indigo-900/30 rounded w-4/5"></div>
            <div className="sr-only">Generating summary...</div>
        </div>
    );
}
