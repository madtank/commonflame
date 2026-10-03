import {
  resolveSpaceAgentWidget,
  resolveSpaceAgentWidgetFromMcp,
  type ResolveSpaceAgentWidgetPayload,
  type SpaceAgentResolvedWidgetResource,
} from "@/lib/space-agent-api";

type WidgetResolver = (
  payload: ResolveSpaceAgentWidgetPayload,
) => Promise<SpaceAgentResolvedWidgetResource>;

type PendingEntry = {
  key: string;
  payload: ResolveSpaceAgentWidgetPayload;
  resolver: WidgetResolver;
  resolve: (value: SpaceAgentResolvedWidgetResource) => void;
  reject: (error: unknown) => void;
};

const DEFAULT_MAX_CONCURRENCY = 2;
const MAX_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [250, 500];
const WIDGET_RESOLVE_TIMEOUT_MS = 10000;

const resolvedWidgetCache = new Map<string, SpaceAgentResolvedWidgetResource>();
const inflightWidgetRequests = new Map<
  string,
  Promise<SpaceAgentResolvedWidgetResource>
>();
const pendingWidgetQueue: PendingEntry[] = [];

let activeWidgetRequests = 0;

function isUuidLike(value: string | null | undefined) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value || "",
  );
}

function isBackendWidgetShellHtml(
  resource: SpaceAgentResolvedWidgetResource | null | undefined,
) {
  const html = resource?.html;
  if (typeof html !== "string" || !html) return false;

  return (
    html.includes("ax-mcp-widget") ||
    html.includes("ax-widget-placeholder") ||
    html.includes("Loading widget from <code>ui://")
  );
}

function mergeResolvedWidgetResource(
  renderable: SpaceAgentResolvedWidgetResource,
  metadataSource: SpaceAgentResolvedWidgetResource | null | undefined,
) {
  if (!metadataSource) return renderable;
  return {
    ...metadataSource,
    ...renderable,
    tool_call_id: metadataSource.tool_call_id ?? renderable.tool_call_id,
    tool_name: metadataSource.tool_name ?? renderable.tool_name,
    tool_action: metadataSource.tool_action ?? renderable.tool_action,
    arguments: metadataSource.arguments ?? renderable.arguments,
    initial_data: metadataSource.initial_data ?? renderable.initial_data,
    tool_result: metadataSource.tool_result ?? renderable.tool_result,
    structured_content:
      metadataSource.structured_content ?? renderable.structured_content,
    result_kind: metadataSource.result_kind ?? renderable.result_kind,
  };
}

export async function resolveWidgetResourceViaCanonicalApi(
  payload: ResolveSpaceAgentWidgetPayload,
) {
  // Launcher-opened widgets are not backed by a stored message, so they use
  // synthetic ids like "launcher-tasks". Skip the message-scoped API resolver
  // for those and read the resource directly from MCP instead.
  if (
    payload.resource_uri.startsWith("ui://") &&
    !isUuidLike(payload.message_id)
  ) {
    return resolveSpaceAgentWidgetFromMcp(payload);
  }

  const timeout = createTimeout(WIDGET_RESOLVE_TIMEOUT_MS);

  try {
    const resolved = await Promise.race([
      resolveSpaceAgentWidget(payload),
      timeout.promise,
    ]);

    // The backend API may return a legacy preview shell or placeholder card
    // instead of the real MCP app HTML when its MCP proxy rejects the caller's
    // auth token. For ui:// resources, treat that shell as a miss and fall back
    // to direct MCP resource reads, which are intentionally unauthenticated.
    if (
      payload.resource_uri.startsWith("ui://") &&
      isBackendWidgetShellHtml(resolved)
    ) {
      const renderable = await resolveSpaceAgentWidgetFromMcp(payload);
      return mergeResolvedWidgetResource(renderable, resolved);
    }

    return resolved;
  } catch (apiError) {
    if (!payload.resource_uri.startsWith("ui://")) {
      throw apiError;
    }

    return resolveSpaceAgentWidgetFromMcp(payload);
  } finally {
    timeout.cancel();
  }
}

function getWidgetResolverKey(payload: ResolveSpaceAgentWidgetPayload) {
  return [
    payload.space_id,
    payload.message_id,
    payload.resource_uri,
    payload.tool_name || "",
    payload.tool_call_id || "",
  ].join("::");
}

function isRetryableWidgetError(error: unknown) {
  const maybeStatus =
    typeof error === "object" && error !== null && "response" in error
      ? Number((error as { response?: { status?: number } }).response?.status)
      : NaN;

  return maybeStatus === 429 || maybeStatus === 503;
}

function wait(ms: number) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

function createTimeout(ms: number) {
  let timer: ReturnType<typeof globalThis.setTimeout> | undefined;
  const promise = new Promise<never>((_, reject) => {
    timer = globalThis.setTimeout(() => {
      reject(new Error("Widget resolve timed out."));
    }, ms);
  });

  return {
    promise,
    cancel() {
      if (timer !== undefined) {
        globalThis.clearTimeout(timer);
        timer = undefined;
      }
    },
  };
}

async function runWidgetResolveWithRetry(
  payload: ResolveSpaceAgentWidgetPayload,
  resolver: WidgetResolver,
) {
  let lastError: unknown;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    try {
      return await resolver(payload);
    } catch (error) {
      lastError = error;
      if (!isRetryableWidgetError(error) || attempt >= MAX_ATTEMPTS - 1) {
        throw error;
      }
      const fallbackDelay = RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1] ?? 500;
      await wait(RETRY_DELAYS_MS[attempt] ?? fallbackDelay);
    }
  }

  throw lastError;
}

function drainWidgetQueue(maxConcurrency = DEFAULT_MAX_CONCURRENCY) {
  while (
    activeWidgetRequests < maxConcurrency &&
    pendingWidgetQueue.length > 0
  ) {
    const next = pendingWidgetQueue.shift();
    if (!next) return;

    activeWidgetRequests += 1;

    void runWidgetResolveWithRetry(next.payload, next.resolver)
      .then((resource) => {
        resolvedWidgetCache.set(next.key, resource);
        next.resolve(resource);
      })
      .catch((error) => {
        next.reject(error);
      })
      .finally(() => {
        inflightWidgetRequests.delete(next.key);
        activeWidgetRequests = Math.max(0, activeWidgetRequests - 1);
        drainWidgetQueue(maxConcurrency);
      });
  }
}

export function resolveWidgetResource(
  payload: ResolveSpaceAgentWidgetPayload,
  resolver: WidgetResolver = resolveWidgetResourceViaCanonicalApi,
) {
  const key = getWidgetResolverKey(payload);
  const cached = resolvedWidgetCache.get(key);
  if (cached) {
    return Promise.resolve(cached);
  }

  const inflight = inflightWidgetRequests.get(key);
  if (inflight) {
    return inflight;
  }

  const request = new Promise<SpaceAgentResolvedWidgetResource>(
    (resolve, reject) => {
      pendingWidgetQueue.push({
        key,
        payload,
        resolver,
        resolve,
        reject,
      });
      drainWidgetQueue();
    },
  );

  inflightWidgetRequests.set(key, request);
  return request;
}

/** Clear a single cached entry so the next resolve re-fetches from the API. */
export function clearWidgetCacheEntry(payload: ResolveSpaceAgentWidgetPayload) {
  const key = getWidgetResolverKey(payload);
  resolvedWidgetCache.delete(key);
  inflightWidgetRequests.delete(key);
}

export function resetWidgetResourceResolverForTests() {
  resolvedWidgetCache.clear();
  inflightWidgetRequests.clear();
  pendingWidgetQueue.splice(0, pendingWidgetQueue.length);
  activeWidgetRequests = 0;
}
