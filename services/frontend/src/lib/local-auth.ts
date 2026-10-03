import { storage } from './storage';
import { config } from '@/config/environment';

export type LocalUser = {
  id: string;
  username: string;
  email: string;
  full_name?: string;
  role: string;
};
export type LocalSession = {
  access_token: string;
  user: LocalUser;
  space_id?: string;
};

export type AccountStatus = {
  auth_mode: 'builtin';
  setup_required: boolean;
  setup_flow: 'browser' | 'token';
  signup: 'open' | 'invite_only' | 'closed';
};
export type AccountDetails = {
  token?: string;
  username: string;
  password: string;
  full_name?: string;
};

export async function getAccountStatus(): Promise<AccountStatus> {
  const response = await fetch('/auth/local/status', { credentials: 'include', cache: 'no-store' });
  if (!response.ok) throw new Error('Could not check account setup. Please try again.');
  const data = await response.json();
  if (data.auth_mode !== 'builtin' || typeof data.setup_required !== 'boolean'
      || !['browser', 'token'].includes(data.setup_flow)
      || !['open', 'invite_only', 'closed'].includes(data.signup)) {
    throw new Error('Account setup is not available on this Waystation.');
  }
  return data;
}

function acceptSession(data: LocalSession): LocalSession {
  if (!data.access_token || !data.user?.username) throw new Error('The server returned an incomplete session.');
  if (storage.getUsername() && storage.getUsername() !== data.user.username) storage.clearSpace();
  storage.setUserToken(data.access_token);
  storage.setUsername(data.user.username);
  storage.setUserMetadata(data.user);
  storage.markSessionActive();
  window.dispatchEvent(new CustomEvent('auth:token-refreshed'));
  return data;
}

function throttled(response: Response, action: string): Error {
  const retryAfter = response.headers.get('Retry-After');
  const seconds = retryAfter && Number.isFinite(Number(retryAfter))
    ? Math.max(1, Math.ceil(Number(retryAfter)))
    : retryAfter && Number.isFinite(Date.parse(retryAfter))
      ? Math.max(1, Math.ceil((Date.parse(retryAfter) - Date.now()) / 1000))
      : null;
  const wait = seconds ? `${seconds} second${seconds === 1 ? '' : 's'}` : 'a minute';
  return new Error(`Too many ${action} attempts. Wait ${wait} before trying again.`);
}

export async function createAccount(details: AccountDetails, kind: 'setup' | 'invite' | 'signup'): Promise<LocalSession> {
  const response = await fetch(kind === 'setup' ? '/auth/local/setup' : '/auth/local/signup', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    credentials: 'include', body: JSON.stringify(details),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 429) throw throttled(response, 'account creation');
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not create your account. Check your details and try again.');
  return acceptSession(data);
}

export type WorkspaceInvitation = { token: string; expires_at: string };
export async function canInviteToWorkspace(): Promise<boolean> {
  const bearer = storage.getUserToken();
  if (!bearer) return false;
  const response = await fetch('/auth/local/invites', {
    credentials: 'include', cache: 'no-store',
    headers: { Authorization: `Bearer ${bearer}` },
  });
  if (!response.ok) return false;
  const data = await response.json();
  return data.can_invite === true;
}
export async function createWorkspaceInvitation(): Promise<WorkspaceInvitation> {
  const bearer = storage.getUserToken();
  if (!bearer) throw new Error('Sign in again before creating an invitation.');
  const response = await fetch('/auth/local/invites', {
    method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${bearer}` },
    body: JSON.stringify({ expires_in_hours: 24 }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not create an invitation. You must be a workspace owner.');
  if (typeof data.token !== 'string' || !data.token || typeof data.expires_at !== 'string') throw new Error('The server returned an incomplete invitation. Please try again.');
  return data;
}

export async function loginLocal(username: string, password: string): Promise<LocalSession> {
  const response = await fetch('/auth/local/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 429) {
    throw throttled(response, 'sign-in');
  }
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not sign in. Check your username and password.');
  }
  return acceptSession(data);
}

export async function logoutLocal(): Promise<void> {
  const response = await fetch('/auth/local/logout', { method: 'POST', credentials: 'include' });
  if (!response.ok) throw new Error('Could not end the server session. Please retry.');
  storage.clearTokens();
  storage.clearAll();
  window.dispatchEvent(new CustomEvent('auth:logout', { detail: { reason: 'signed_out' } }));
  try {
    const channel = new BroadcastChannel(`waystation-auth-${config.environment}`);
    channel.postMessage({ type: 'logout' });
    channel.close();
  } catch { /* sign-out remains valid if channel access is blocked */ }
}
