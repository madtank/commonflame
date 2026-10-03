import { useState, useEffect, useCallback, useRef } from "react";
import { storage, type StoredOrganization } from "@/lib/storage";

interface PersistedMessageState {
  readMessageIds: string[];
  expandedPosts: number[];
  scrollPosition: number;
  drafts: Record<string, string>;
  lastReadTimestamp: number;
}

interface MessageStateStore {
  version: number;
  spaces: Record<string, PersistedMessageState>;
}

const STORAGE_KEY = "ax-message-state";
const CURRENT_VERSION = 2;
const GLOBAL_SPACE_KEY = "global";

const createDefaultState = (): PersistedMessageState => ({
  readMessageIds: [],
  expandedPosts: [],
  scrollPosition: 0,
  drafts: {},
  lastReadTimestamp: Date.now(),
});

const normalizeState = (
  state?: Partial<PersistedMessageState>,
): PersistedMessageState => {
  const defaults = createDefaultState();
  return {
    readMessageIds: Array.isArray(state?.readMessageIds)
      ? [...state.readMessageIds]
      : defaults.readMessageIds,
    expandedPosts: Array.isArray(state?.expandedPosts)
      ? [...state.expandedPosts]
      : defaults.expandedPosts,
    scrollPosition:
      typeof state?.scrollPosition === "number"
        ? state.scrollPosition
        : defaults.scrollPosition,
    drafts:
      state?.drafts && typeof state.drafts === "object"
        ? { ...state.drafts }
        : defaults.drafts,
    lastReadTimestamp:
      typeof state?.lastReadTimestamp === "number"
        ? state.lastReadTimestamp
        : Date.now(),
  };
};

const loadStore = (): { store: MessageStateStore; needsSave: boolean } => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return { store: { version: CURRENT_VERSION, spaces: {} }, needsSave: false };
    }

    const parsed = JSON.parse(stored);
    if (parsed && typeof parsed === "object") {
      if (
        parsed.version === CURRENT_VERSION &&
        parsed.spaces &&
        typeof parsed.spaces === "object"
      ) {
        return {
          store: {
            version: CURRENT_VERSION,
            spaces: parsed.spaces as Record<string, PersistedMessageState>,
          },
          needsSave: false,
        };
      }

      // Legacy single-space state – migrate into new structure
      const legacyState = normalizeState(parsed as PersistedMessageState);
      return {
        store: {
          version: CURRENT_VERSION,
          spaces: { [GLOBAL_SPACE_KEY]: legacyState },
        },
        needsSave: true,
      };
    }
  } catch (error) {
    console.error("Failed to load persisted message state:", error);
  }

  return { store: { version: CURRENT_VERSION, spaces: {} }, needsSave: false };
};

const persistStore = (store: MessageStateStore) => {
  try {
    const payload: MessageStateStore = {
      version: CURRENT_VERSION,
      spaces: store.spaces ?? {},
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.error("Failed to persist message state:", error);
  }
};

const loadStateForSpace = (spaceKey: string): PersistedMessageState => {
  const { store, needsSave } = loadStore();
  const existing = store.spaces?.[spaceKey];
  const normalized = normalizeState(existing);

  if (!store.spaces || typeof store.spaces !== "object") {
    store.spaces = {};
  }

  store.spaces[spaceKey] = normalized;
  if (needsSave || !existing) {
    persistStore(store);
  }

  return normalized;
};

const persistStateForSpace = (spaceKey: string, state: PersistedMessageState) => {
  const { store } = loadStore();
  if (!store.spaces || typeof store.spaces !== "object") {
    store.spaces = {};
  }
  store.spaces[spaceKey] = normalizeState(state);
  persistStore(store);
};

const getSpaceKeyFromOrganization = (
  org?: StoredOrganization | null,
): string => {
  if (org?.id) return `org:${org.id}`;
  if (org?.slug) return `slug:${org.slug}`;
  return GLOBAL_SPACE_KEY;
};

const getInitialSpaceKey = () =>
  storage.getCurrentOrganizationKey?.() ??
  getSpaceKeyFromOrganization(storage.getCurrentOrganization?.());

export function useMessageState() {
  const initialSpaceKey = getInitialSpaceKey();
  const [spaceKey, setSpaceKey] = useState<string>(initialSpaceKey);
  const [state, setState] = useState<PersistedMessageState>(() =>
    loadStateForSpace(initialSpaceKey),
  );
  const skipNextPersistRef = useRef(false);
  const spaceKeyRef = useRef(spaceKey);

  useEffect(() => {
    spaceKeyRef.current = spaceKey;
  }, [spaceKey]);

  // Respond to space change events so drafts/read-state are scoped per space
  useEffect(() => {
    const handleSpaceChange = (event: Event) => {
      const detail = (event as CustomEvent<{ spaceKey?: string }>).detail;
      const nextKey =
        detail?.spaceKey ?? storage.getCurrentOrganizationKey?.() ?? GLOBAL_SPACE_KEY;

      if (spaceKeyRef.current === nextKey) return;

      spaceKeyRef.current = nextKey;
      setSpaceKey(nextKey);
      setState(loadStateForSpace(nextKey));
    };

    window.addEventListener("spaces:current-changed", handleSpaceChange as EventListener);
    return () =>
      window.removeEventListener(
        "spaces:current-changed",
        handleSpaceChange as EventListener,
      );
  }, []);

  // Persist whenever the scoped state changes
  useEffect(() => {
    if (skipNextPersistRef.current) {
      skipNextPersistRef.current = false;
      return;
    }
    persistStateForSpace(spaceKey, state);
  }, [spaceKey, state]);

  // Mark message as read
  const markAsRead = useCallback((messageId: string) => {
    setState((prev) => ({
      ...prev,
      readMessageIds: prev.readMessageIds.includes(messageId)
        ? prev.readMessageIds
        : [...prev.readMessageIds, messageId],
      lastReadTimestamp: Date.now(),
    }));
  }, []);

  // Mark multiple messages as read
  const markMultipleAsRead = useCallback((messageIds: string[]) => {
    setState((prev) => ({
      ...prev,
      readMessageIds: Array.from(
        new Set([...prev.readMessageIds, ...messageIds]),
      ),
      lastReadTimestamp: Date.now(),
    }));
  }, []);

  // Toggle post expansion
  const togglePostExpansion = useCallback((postId: number) => {
    setState((prev) => ({
      ...prev,
      expandedPosts: prev.expandedPosts.includes(postId)
        ? prev.expandedPosts.filter((id) => id !== postId)
        : [...prev.expandedPosts, postId],
    }));
  }, []);

  // Save draft message
  const saveDraft = useCallback((channel: string, content: string) => {
    setState((prev) => ({
      ...prev,
      drafts: {
        ...prev.drafts,
        [channel]: content,
      },
    }));
  }, []);

  // Clear draft
  const clearDraft = useCallback((channel: string) => {
    setState((prev) => {
      const newDrafts = { ...prev.drafts };
      delete newDrafts[channel];
      return {
        ...prev,
        drafts: newDrafts,
      };
    });
  }, []);

  // Save scroll position
  const saveScrollPosition = useCallback((position: number) => {
    setState((prev) => ({
      ...prev,
      scrollPosition: position,
    }));
  }, []);

  // Check if message is read
  const isMessageRead = useCallback(
    (messageId: string) => state.readMessageIds.includes(messageId),
    [state.readMessageIds],
  );

  // Check if post is expanded
  const isPostExpanded = useCallback(
    (postId: number) => state.expandedPosts.includes(postId),
    [state.expandedPosts],
  );

  // Get draft for channel
  const getDraft = useCallback(
    (channel: string) => state.drafts[channel] || "",
    [state.drafts],
  );

  // Clear all state (for logout)
  const clearAllState = useCallback(() => {
    skipNextPersistRef.current = true;
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      console.error("Failed to clear message state:", error);
    }
    setState(createDefaultState());
  }, []);

  return {
    // State
    readMessageIds: state.readMessageIds,
    expandedPosts: state.expandedPosts,
    scrollPosition: state.scrollPosition,
    drafts: state.drafts,
    lastReadTimestamp: state.lastReadTimestamp,

    // Actions
    markAsRead,
    markMultipleAsRead,
    togglePostExpansion,
    saveDraft,
    clearDraft,
    saveScrollPosition,

    // Helpers
    isMessageRead,
    isPostExpanded,
    getDraft,
    clearAllState,
  };
}
