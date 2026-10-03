import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Users, ArrowRight, X } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";

interface JoinSpaceInterstitialProps {
    orgName: string;
    orgSlug: string;
    memberCount: number;
    description?: string | null;
    onJoin: () => Promise<void>;
    onCancel: () => void;
    isOpen: boolean;
}

export function JoinSpaceInterstitial({
    orgName,
    orgSlug,
    memberCount,
    description,
    onJoin,
    onCancel,
    isOpen
}: JoinSpaceInterstitialProps) {
    const [isJoining, setIsJoining] = useState(false);

    const handleJoin = async () => {
        setIsJoining(true);
        try {
            await onJoin();
        } catch (error) {
            setIsJoining(false);
            // Error handling should be done by parent or toast
        }
    };

    return (
        <Dialog open={isOpen} onOpenChange={(open) => !open && onCancel()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="text-xl flex items-center gap-2">
                        Join {orgName}
                    </DialogTitle>
                    <DialogDescription>
                        You've been invited to join this workspace.
                    </DialogDescription>
                </DialogHeader>

                <div className="py-6 flex flex-col items-center text-center space-y-4">
                    <div className="w-16 h-16 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center mb-2">
                        <Users className="w-8 h-8 text-blue-600 dark:text-blue-400" />
                    </div>

                    <div className="space-y-1">
                        <h3 className="font-semibold text-lg">{orgName}</h3>
                        <p className="text-sm text-gray-500 dark:text-gray-400">@{orgSlug}</p>
                    </div>

                    {description && (
                        <p className="text-sm text-gray-600 dark:text-gray-300 max-w-sm">
                            {description}
                        </p>
                    )}

                    <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-100 dark:bg-gray-800 px-3 py-1 rounded-full">
                        <Users className="w-3 h-3" />
                        <span>{memberCount} member{memberCount !== 1 ? 's' : ''}</span>
                    </div>
                </div>

                <DialogFooter className="flex-col sm:justify-center gap-2 sm:gap-2">
                    <Button
                        className="w-full sm:w-full bg-blue-600 hover:bg-blue-700 text-white gap-2 h-11"
                        onClick={handleJoin}
                        disabled={isJoining}
                    >
                        {isJoining ? (
                            "Joining..."
                        ) : (
                            <>
                                Join Workspace
                                <ArrowRight className="w-4 h-4" />
                            </>
                        )}
                    </Button>
                    <Button
                        variant="ghost"
                        className="w-full sm:w-full text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                        onClick={onCancel}
                        disabled={isJoining}
                    >
                        Cancel
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
