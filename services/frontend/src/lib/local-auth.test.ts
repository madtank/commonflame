import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loginLocal, logoutLocal } from './local-auth';
import { storage } from './storage';

describe('local authentication client', () => {
  beforeEach(() => { storage.clearTokens(); vi.restoreAllMocks(); });
  it('posts credentials to this installation and stores only its access session', async () => {
    const user = { id: 'user-1', username: 'owner', email: 'owner@example.test', role: 'admin' };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ access_token: 'session-token', user }), { status: 200 }));
    await loginLocal('owner', 'synthetic-test-password');
    expect(fetchMock).toHaveBeenCalledWith('/auth/local/login', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'owner', password: 'synthetic-test-password' }),
    });
    expect(storage.getUserToken()).toBe('session-token');
    expect(storage.getRefreshToken()).toBeNull();
  });
  it('rejects an unsuccessful sign-in without creating a local session', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'Invalid username or password.' }), { status: 401 }));
    await expect(loginLocal('owner', 'incorrect')).rejects.toThrow('Invalid username or password.');
    expect(storage.getUserToken()).toBeNull();
  });
  it('explains the server sign-in throttle using Retry-After', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 429, headers: { 'Retry-After': '20' } }));
    await expect(loginLocal('owner', 'incorrect')).rejects.toThrow('Too many sign-in attempts. Wait 20 seconds before trying again.');
    expect(storage.getUserToken()).toBeNull();
  });
  it('preserves the session when server logout fails so the user can retry', async () => {
    storage.setUserToken('session-token');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 503 }));
    await expect(logoutLocal()).rejects.toThrow('Could not end the server session.');
    expect(storage.getUserToken()).toBe('session-token');
  });
  it('clears local access after the server revokes the refresh session', async () => {
    storage.setUserToken('session-token');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
    await logoutLocal();
    expect(storage.getUserToken()).toBeNull();
  });
});
