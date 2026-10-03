const APPROVAL_PATHS = new Set([
  '/oauth/authorize',
  '/device',
  '/auth/device/verify',
]);

/** Only return to a native approval page on this Waystation. */
export function validateApprovalReturnPath(value: string | null): string | null {
  if (!value || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020\u007f]/.test(value)) return null;
  const rawPath = value.split(/[?#]/, 1)[0];
  if (/%(?:2f|5c|0[0-9a-f]|1[0-9a-f]|7f)/i.test(rawPath)) return null;
  try {
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin || !APPROVAL_PATHS.has(url.pathname) || url.hash) return null;
    const secretNames = new Set(['token', 'access_token', 'refresh_token', 'password', 'setup_token', 'invite_token']);
    if ([...url.searchParams.keys()].some(name => secretNames.has(name.toLowerCase()))) return null;
    return url.pathname + url.search;
  } catch { return null; }
}

export function getApprovalReturnPath(search = window.location.search): string | null {
  return validateApprovalReturnPath(new URLSearchParams(search).get('next'));
}

export function accountEntryHref(path: '/login' | '/signup', next: string | null = null): string {
  const target = validateApprovalReturnPath(next);
  return target ? `${path}?${new URLSearchParams({ next: target })}` : path;
}
