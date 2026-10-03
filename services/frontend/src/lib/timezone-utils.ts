import { formatDistanceToNow, format } from "date-fns";
import { formatInTimeZone } from "date-fns-tz";

export const formatRelativeTime = (timestamp: string): string => {
  try {
    // Check if timestamp is valid
    if (!timestamp || typeof timestamp !== 'string') {
      console.error("formatRelativeTime: Invalid timestamp provided:", timestamp);
      return "Unknown time";
    }

    // Backend returns naive UTC timestamps - always treat as UTC for cloud deployment
    // If no timezone indicator, assume UTC (append 'Z')
    let dateString = timestamp;
    if (!timestamp.includes('Z') && !timestamp.includes('+') && !timestamp.includes('-', 10)) {
      dateString = timestamp + 'Z';
    }

    const date = new Date(dateString);

    // Check if the date is valid
    if (isNaN(date.getTime())) {
      console.error("formatRelativeTime: Failed to parse timestamp:", timestamp);
      return "Invalid date";
    }

    // Use date-fns formatDistanceToNow for consistent relative time formatting
    return formatDistanceToNow(date, { addSuffix: true });

  } catch (error) {
    console.error("Error formatting relative time:", error);
    return "Unknown time";
  }
};

export const formatTimestamp = (timestamp: string): string => {
  try {
    // Check if timestamp is valid
    if (!timestamp || typeof timestamp !== 'string') {
      console.error("formatTimestamp: Invalid timestamp provided:", timestamp);
      return "Invalid date";
    }

    // Parse the timestamp assuming it's UTC
    const date = new Date(timestamp + "Z"); // Add Z to ensure UTC parsing
    if (isNaN(date.getTime())) {
      return "Invalid date";
    }
    // Always render in UTC
    return formatInTimeZone(date, 'UTC', 'yyyy-MM-dd HH:mm:ss');
  } catch (error) {
    console.error("Error formatting timestamp:", error);
    return "Invalid date";
  }
};
