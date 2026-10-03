import { beforeEach, describe, expect, it, vi } from 'vitest';
import { canInviteToWorkspace, createAccount, createWorkspaceInvitation, getAccountStatus, loginLocal, logoutLocal } from './local-auth';
import { storage } from './storage';

describe('Waystation account authentication client', () => {
  beforeEach(() => { storage.clearTokens(); vi.restoreAllMocks(); });
  it('checks invite-only setup status without caching it', async () => {
    const status = { auth_mode: 'builtin', setup_required: true, signup: 'invite_only', setup_flow: 'token' };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(status)));
    await expect(getAccountStatus()).resolves.toEqual(status);
    expect(fetchMock).toHaveBeenCalledWith('/auth/local/status', { credentials: 'include', cache: 'no-store' });
  });
  it.each(['setup', 'invite'] as const)('consumes a %s token only in the request body', async kind => {
    const details = { token: 'synthetic-one-time-token', username: 'owner', password: 'synthetic-test-password' };
    const user = { id: 'user-1', username: 'owner', email: 'owner@example.test', role: 'user' };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ access_token: 'session-token', user })));
    const persistent = vi.spyOn(localStorage, 'setItem');
    const tabStorage = vi.spyOn(sessionStorage, 'setItem');
    await createAccount(details, kind);
    expect(fetchMock).toHaveBeenCalledWith(kind === 'setup' ? '/auth/local/setup' : '/auth/local/signup', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(details),
    });
    const saved = [...persistent.mock.calls, ...tabStorage.mock.calls].map(([, value]) => value).join('');
    expect(saved).not.toContain(details.token);
    expect(saved).not.toContain(details.password);
    expect(storage.getUserToken()).toBe('session-token');
  });
  it('does not create an account session from an expired or invalid one-time token', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'Setup or invitation token is invalid or expired' }), { status: 400 }));
    await expect(createAccount({ token: 'expired-synthetic-token', username: 'owner', password: 'synthetic-password' }, 'invite')).rejects.toThrow('invalid or expired');
    expect(storage.getUserToken()).toBeNull();
  });
  it('creates a private account without requiring or inventing an invitation', async () => {
    const details = { username: 'another', password: 'a-test-passphrase' };
    const user = { id: 'user-2', username: 'another', email: 'another@waystation.local', role: 'user' };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ access_token: 'session-token', user })));
    await createAccount(details, 'signup');
    expect(fetchMock.mock.calls[0][0]).toBe('/auth/local/signup');
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual(details);
  });
  it('rejects an unknown registration policy instead of showing open signup', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ auth_mode: 'builtin', setup_required: false, signup: 'surprise', setup_flow: 'browser' })));
    await expect(getAccountStatus()).rejects.toThrow('not available');
  });
  it('asks the backend for workspace invitation permission as the signed-in human', async () => {
    storage.setUserToken('session-token');
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ can_invite: true })));
    await expect(canInviteToWorkspace()).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith('/auth/local/invites', { credentials: 'include', cache: 'no-store', headers: { Authorization: 'Bearer session-token' } });
  });
  it('creates a private one-time invitation without saving it', async () => {
    storage.setUserToken('session-token');
    const invitation = { token: 'synthetic-invitation-token', expires_at: '2030-01-01T00:00:00Z' };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(invitation)));
    const persistent = vi.spyOn(localStorage, 'setItem');
    const tabStorage = vi.spyOn(sessionStorage, 'setItem');
    await expect(createWorkspaceInvitation()).resolves.toEqual(invitation);
    expect(fetchMock).toHaveBeenCalledWith('/auth/local/invites', {
      method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer session-token' }, body: JSON.stringify({ expires_in_hours: 24 }),
    });
    expect(persistent).not.toHaveBeenCalled();
    expect(tabStorage).not.toHaveBeenCalled();
  });
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
  it('explains signup throttling so a user can retry without changing their details', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 429, headers: { 'Retry-After': '10' } }));
    await expect(createAccount({ username: 'another', password: 'a-test-passphrase' }, 'signup')).rejects.toThrow('Too many account creation attempts. Wait 10 seconds before trying again.');
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
