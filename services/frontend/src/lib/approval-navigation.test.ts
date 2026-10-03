import { describe, expect, it } from 'vitest';
import { accountEntryHref, getApprovalReturnPath, validateApprovalReturnPath } from './approval-navigation';

describe('approval return navigation', () => {
  it.each(['/oauth/authorize?client_id=agent&redirect_uri=https%3A%2F%2Fclient.example%2Fcallback&state=public-state', '/device?user_code=TEST-CODE', '/auth/device/verify?user_code=TEST-CODE'])('accepts the known approval path %s', path => {
    expect(validateApprovalReturnPath(path)).toBe(path);
    expect(getApprovalReturnPath(`?${new URLSearchParams({ next: path })}`)).toBe(path);
  });
  it.each(['https://evil.example/device', '//evil.example/device', '/\\evil.example/device', 'javascript:alert(1)', '/app', '/admin', '/oauth/token', '/oauth/authorize#token', '/%2f%2fevil.example/device', '/device\n', '/device?access_token=secret', '/device?setup_token=secret', '/device?invite_token=secret'])('rejects an unsafe or unrelated target %s', path => {
    expect(validateApprovalReturnPath(path)).toBeNull();
    expect(accountEntryHref('/login', path)).toBe('/login');
  });
  it('keeps an account URL free of secrets and carries only its validated approval path', () => {
    const next = '/device?user_code=TEST-CODE';
    const result = new URL(accountEntryHref('/signup', next), window.location.origin);
    expect(result.pathname).toBe('/signup');
    expect([...result.searchParams.keys()]).toEqual(['next']);
    expect(result.searchParams.get('next')).toBe(next);
  });
});
