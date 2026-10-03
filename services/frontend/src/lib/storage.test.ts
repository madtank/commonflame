import { beforeEach, describe, expect, it, vi } from 'vitest';
import { storage, migrateStorage } from './storage';
import { config } from '@/config/environment';

beforeEach(() => { storage.clearTokens(); localStorage.clear(); sessionStorage.clear(); });

describe('local session storage', () => {
  it('keeps access tokens in tab storage, never persistent localStorage', () => {
    storage.setUserToken('access-token');
    expect(storage.getUserToken()).toBe('access-token');
    expect(sessionStorage.getItem(`waystation_${config.environment}_userToken`)).toBe('access-token');
    expect(localStorage.getItem(`waystation_${config.environment}_userToken`)).toBeNull();
  });
  it('does not import old application credentials', () => {
    localStorage.setItem('userToken', 'stale-token');
    localStorage.setItem('ax_development_userToken', 'old-token');
    migrateStorage();
    expect(storage.getUserToken()).toBeNull();
  });
  it('never exposes or persists refresh tokens', () => {
    storage.setTokens('access-token', 'must-stay-in-cookie');
    expect(storage.getRefreshToken()).toBeNull();
    expect(localStorage.getItem(`waystation_${config.environment}_refreshToken`)).toBeNull();
  });
  it('rotates a server session with credentials and updates the local identity', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ access_token: 'rotated-token', user: { username: 'owner', email: 'owner@example.test', role: 'admin' } }), { status: 200 }));
    expect(await storage._doRefreshTokens()).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith('/auth/local/refresh', { method: 'POST', credentials: 'include' });
    expect(storage.getUserToken()).toBe('rotated-token');
    expect(storage.getUsername()).toBe('owner');
    fetchMock.mockRestore();
  });
  it('rejects an unsuccessful refresh without storing a response token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 401 }));
    expect(await storage._doRefreshTokens()).toBe(false);
    expect(storage.getUserToken()).toBeNull();
    fetchMock.mockRestore();
  });
  it('expires JWTs using base64url claims', () => {
    const payload = btoa(JSON.stringify({ exp: 1 })).replace(/=/g, '');
    expect(storage.isTokenExpired(`header.${payload}.signature`)).toBe(true);
  });
});
