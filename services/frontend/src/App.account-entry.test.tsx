import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';

const { signIn, createAccount, getAccountStatus } = vi.hoisted(() => ({
  signIn: vi.fn(), createAccount: vi.fn(), getAccountStatus: vi.fn(),
}));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({
  isLoading: false, isAuthenticated: true, token: 'synthetic-existing-session',
  user: { username: 'existing', attributes: { id: 'existing-id', role: 'user' } },
  signIn, signOut: vi.fn(),
}) }));
vi.mock('@/hooks/useTokenRefresh', () => ({ useTokenRefresh: vi.fn() }));
vi.mock('@/lib/local-auth', () => ({ getAccountStatus, createAccount, loginLocal: vi.fn() }));
vi.mock('@/pages/AxPlatformPage', () => ({ default: () => <div>Existing workspace</div> }));

function openApp(path: string) {
  window.history.replaceState({}, '', path);
  return render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>);
}

describe('Account entry with an existing browser session', () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: false, setup_flow: 'browser', signup: 'open' });
  });
  it('allows another account to be created rather than silently returning to the workspace', async () => {
    createAccount.mockResolvedValue({ access_token: 'synthetic-new-session', user: { username: 'another' } });
    openApp('/signup');
    expect(await screen.findByRole('heading', { name: 'Create your account' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'another' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'synthetic-passphrase' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'synthetic-passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(signIn).toHaveBeenCalledWith('synthetic-new-session', 'another'));
    expect(window.location.pathname).toBe('/app');
    expect(await screen.findByText('Existing workspace')).toBeInTheDocument();
  });
  it('keeps the existing workspace accessible and does not silently sign out', async () => {
    openApp('/login');
    expect(await screen.findByText(/Signed in as existing/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Return to workspace' })).toHaveAttribute('href', '/app');
    expect(signIn).not.toHaveBeenCalled();
  });
});
