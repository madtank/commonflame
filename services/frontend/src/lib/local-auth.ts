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

export async function loginLocal(username: string, password: string): Promise<LocalSession> {
  const response = await fetch('/auth/local/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 429) {
    const retryAfter = response.headers.get('Retry-After');
    const seconds = retryAfter && Number.isFinite(Number(retryAfter))
      ? Math.max(1, Math.ceil(Number(retryAfter)))
      : retryAfter && Number.isFinite(Date.parse(retryAfter))
        ? Math.max(1, Math.ceil((Date.parse(retryAfter) - Date.now()) / 1000))
        : null;
    const wait = seconds ? `${seconds} second${seconds === 1 ? '' : 's'}` : 'a minute';
    throw new Error(`Too many sign-in attempts. Wait ${wait} before trying again.`);
  }
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not sign in. Check your username and password.');
  }
  if (!data.access_token || !data.user?.username) throw new Error('The server returned an incomplete session.');
  if (storage.getUsername() && storage.getUsername() !== data.user.username) storage.clearSpace();
  storage.setUserToken(data.access_token);
  storage.setUsername(data.user.username);
  storage.setUserMetadata(data.user);
  storage.markSessionActive();
  window.dispatchEvent(new CustomEvent('auth:token-refreshed'));
  return data;
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
  } catch { /* local sign-out remains valid if channel access is blocked */ }
}
