import React, { useState, useEffect, useRef, useCallback } from "react";
import { Bell, Check, CheckSquare, AtSign } from "lucide-react";
import { Button } from "./ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { api } from "@/lib/api-clean";
import { storage } from "@/lib/storage";
import { useNavigate } from "react-router-dom";

interface Notification {
  id: string;
  message: string;
  space_id: string;
  space_name: string;
  message_id: string;
  timestamp: string;
  read: boolean;
  mentioned_agent?: string;
  // New fields for enhanced notification system
  notification_type?: "mention" | "task_assignment";
  mentioned_user?: string; // Set when a human user is mentioned (vs agent)
  task_id?: string; // For task assignment notifications
  task_status?: string; // Track if task is complete (for persistent alerts)
  sender_type?: "user" | "agent"; // Who sent the message that triggered this
}

interface NotificationsResponse {
  unread_count: number;
  notifications: Notification[];
}

interface NotificationBellProps {
  currentOrgId?: string;
}

export const NotificationBell: React.FC<NotificationBellProps> = ({
  currentOrgId,
}) => {
  const navigate = useNavigate();
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { user } = useAuth(); // Use user object to check auth status, token handled by api client
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Calculate unread count from actual notifications (not server's potentially stale count)
  // Task notifications count as unread until the task is completed
  const unreadCount = (notifications ?? []).filter((n) => {
    if (n.notification_type === "task_assignment") {
      // Task notifications stay "unread" until task is complete
      return n.task_status !== "completed";
    }
    return !n.read;
  }).length;

  // Fetch notifications from API - filtered by current space
  const fetchNotifications = useCallback(async () => {
    if (!user) return;

    setLoading(true);
    setError(null);

    try {
      // Pass space_id to get notifications for current space only
      const data: NotificationsResponse = await api.getNotifications(
        currentOrgId ? { space_id: currentOrgId } : undefined,
      );

      // Filter to only show notifications for current space (defensive - backend should handle this)
      // Also ensures we don't show stale notifications from other spaces
      const allNotifications = data.notifications ?? [];
      const relevantNotifications = currentOrgId
        ? allNotifications.filter((n) => n.space_id === currentOrgId)
        : allNotifications;

      const parseTimestamp = (value?: string | null) => {
        if (!value) return null;
        const ms = Date.parse(value);
        return Number.isFinite(ms) ? ms : null;
      };

      const globalClearedAt = parseTimestamp(
        storage.getNotificationsClearedAt(),
      );
      const orgClearedAt = currentOrgId
        ? parseTimestamp(storage.getNotificationsClearedAt(currentOrgId))
        : null;
      const clearedAtCandidates = [globalClearedAt, orgClearedAt].filter(
        (value): value is number => typeof value === "number",
      );
      const clearedAtMs = clearedAtCandidates.length
        ? Math.max(...clearedAtCandidates)
        : null;

      const normalizedNotifications = clearedAtMs
        ? relevantNotifications.map((notification) => {
            const ts = parseTimestamp(notification.timestamp);
            if (ts && ts <= clearedAtMs) {
              return { ...notification, read: true };
            }
            return notification;
          })
        : relevantNotifications;

      setNotifications(normalizedNotifications);
      // Note: unreadCount is now computed from notifications, not set from server
    } catch (err) {
      console.error("Error fetching notifications:", err);
      setError("Failed to load");
    } finally {
      setLoading(false);
    }
  }, [user, currentOrgId]);

  // Fetch on mount, when space changes, and when opened
  useEffect(() => {
    if (user) {
      fetchNotifications();
    }
  }, [user, currentOrgId, fetchNotifications]);

  useEffect(() => {
    if (isOpen && user) {
      fetchNotifications();
    }
  }, [isOpen, user, fetchNotifications]);

  // Listen for notification updates via custom event (from SSE)
  useEffect(() => {
    const handleNotificationUpdate = () => {
      fetchNotifications();
    };
    window.addEventListener("ax:notification-update", handleNotificationUpdate);
    return () =>
      window.removeEventListener(
        "ax:notification-update",
        handleNotificationUpdate,
      );
  }, [fetchNotifications]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Format timestamp
  const formatTime = (timestamp: string) => {
    if (!timestamp) return "";
    try {
      const date = new Date(timestamp);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);

      if (diffMins < 1) return "just now";
      if (diffMins < 60) return `${diffMins}m ago`;
      if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
      return `${Math.floor(diffMins / 1440)}d ago`;
    } catch {
      return "";
    }
  };

  const handleMarkAllRead = async () => {
    const unreadIds = notifications.filter((n) => !n.read).map((n) => n.id);
    if (unreadIds.length === 0) return;

    console.log(
      "[Notifications] Marking all as read:",
      unreadIds.length,
      "notifications",
    );

    // Optimistic update - mark all as read locally (unreadCount auto-updates since it's computed)
    const previousNotifications = [...notifications];
    const updated = notifications.map((n) => ({ ...n, read: true }));
    setNotifications(updated);

    try {
      // API call to mark ALL as read (server-side)
      console.log("[Notifications] Calling markAllNotificationsAsRead...");
      const okAll = await api.markAllNotificationsAsRead();
      console.log("[Notifications] markAllNotificationsAsRead result:", okAll);

      if (okAll) {
        const clearedAt = new Date().toISOString();
        storage.setNotificationsClearedAt(clearedAt);
        if (currentOrgId) {
          storage.setNotificationsClearedAt(clearedAt, currentOrgId);
        }
        // Don't refetch immediately - trust the optimistic update
        // Only refetch after a delay to catch any new notifications
        setTimeout(() => fetchNotifications(), 2000);
        return;
      }

      // Fallback: older backends only support batch by UUID
      console.log("[Notifications] Falling back to batch mark...");
      const uuidIds = unreadIds.filter((id) =>
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
          id,
        ),
      );
      const okBatch = await api.markAllAsRead(uuidIds);
      console.log("[Notifications] markAllAsRead batch result:", okBatch);

      if (okBatch) {
        const clearedAt = new Date().toISOString();
        storage.setNotificationsClearedAt(clearedAt);
        if (currentOrgId) {
          storage.setNotificationsClearedAt(clearedAt, currentOrgId);
        }
        setTimeout(() => fetchNotifications(), 2000);
        return;
      }

      // If both fail, revert to previous state
      console.error(
        "[Notifications] All mark-as-read methods failed, reverting",
      );
      setNotifications(previousNotifications);
    } catch (err) {
      console.error("[Notifications] Error marking all as read:", err);
      // Revert optimistic update on failure
      setNotifications(previousNotifications);
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <Button
        variant="ghost"
        size="sm"
        aria-label="Notifications"
        onClick={() => setIsOpen(!isOpen)}
        className="p-2 relative"
      >
        <Bell className="h-4 w-4" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 h-5 w-5 bg-red-600 text-white text-xs rounded-full flex items-center justify-center font-medium">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </Button>

      {isOpen && (
        <div className="absolute right-0 mt-2 w-80 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
            <h3 className="font-semibold">Notifications</h3>
            {unreadCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="h-auto p-1 text-xs text-blue-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                onClick={handleMarkAllRead}
              >
                <Check className="w-3 h-3 mr-1" />
                Mark all read
              </Button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <div className="p-4 text-center text-gray-500">Loading...</div>
            ) : error ? (
              <div className="p-4 text-center text-red-500 text-sm">
                {error}
              </div>
            ) : notifications.length === 0 ? (
              <div className="p-4 text-center text-gray-500">
                No notifications
              </div>
            ) : (
              notifications.map((notification) => {
                const isTaskNotification =
                  notification.notification_type === "task_assignment";
                const isTaskComplete = notification.task_status === "completed";

                // Task notifications stay unread/highlighted until task is complete
                const effectivelyRead = isTaskNotification
                  ? isTaskComplete
                  : notification.read;

                return (
                  <div
                    key={notification.id}
                    className={`p-4 border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer ${
                      !effectivelyRead
                        ? isTaskNotification
                          ? "bg-purple-50 dark:bg-purple-900/20 border-l-4 border-l-purple-500"
                          : "bg-blue-50 dark:bg-blue-900/20"
                        : ""
                    }`}
                    onClick={async (e) => {
                      e.stopPropagation();

                      // Task notifications: don't mark as read until task complete
                      if (!isTaskNotification) {
                        // Mark as read locally (unreadCount auto-updates since it's computed)
                        const updated = notifications.map((n) =>
                          n.id === notification.id ? { ...n, read: true } : n,
                        );
                        setNotifications(updated);

                        // Mark as read on server (fire and forget)
                        if (!notification.read) {
                          api.markAsRead(notification.id).catch(console.error);
                        }
                      }

                      // Switch space if needed
                      const currentOrgId = storage.getCurrentOrgId();
                      if (
                        notification.space_id &&
                        currentOrgId !== notification.space_id
                      ) {
                        try {
                          await api.switchOrganization(notification.space_id);
                          // Add artificial delay for UX and token callback
                          await new Promise((r) => setTimeout(r, 200));
                          // Navigate to appropriate page
                          const targetPage = isTaskNotification
                            ? "/tasks"
                            : "/messages";
                          window.location.href = targetPage;
                          return;
                        } catch (err) {
                          console.error("Failed to switch org", err);
                        }
                      }

                      // Navigate to appropriate page
                      setIsOpen(false);
                      navigate(isTaskNotification ? "/tasks" : "/messages");
                    }}
                  >
                    <div className="flex items-start gap-2">
                      {/* Icon based on notification type */}
                      <div
                        className={`flex-shrink-0 mt-0.5 ${
                          isTaskNotification
                            ? "text-purple-500"
                            : "text-blue-500"
                        }`}
                      >
                        {isTaskNotification ? (
                          <CheckSquare className="w-4 h-4" />
                        ) : (
                          <AtSign className="w-4 h-4" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm line-clamp-2">
                          {notification.message}
                        </p>
                        <div className="flex justify-between items-center mt-1">
                          <span className="text-xs text-gray-500">
                            {isTaskNotification ? (
                              <span className="flex items-center gap-1">
                                <span className="font-medium text-purple-600 dark:text-purple-400">
                                  Task assigned
                                </span>
                                {!isTaskComplete && (
                                  <span className="text-[10px] px-1 py-0.5 bg-purple-100 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded">
                                    Active
                                  </span>
                                )}
                              </span>
                            ) : (
                              notification.mentioned_agent &&
                              `@${notification.mentioned_agent}`
                            )}
                          </span>
                          <span className="text-xs text-gray-400">
                            {formatTime(notification.timestamp)}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default NotificationBell;
