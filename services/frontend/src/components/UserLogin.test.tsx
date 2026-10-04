import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UserLogin } from './UserLogin';
const { loginLocal, getAccountStatus, createAccount } = vi.hoisted(() => ({ loginLocal: vi.fn(), getAccountStatus: vi.fn(), createAccount: vi.fn() }));
vi.mock('@/lib/local-auth', () => ({ loginLocal, getAccountStatus, createAccount }));

const password = 'a-unique-password';
const token = 'synthetic-one-time-token';
function enterAccount(tokenLabel: string) {
  fireEvent.change(screen.getByLabelText(tokenLabel), { target: { value: token } });
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'new-owner' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
  fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: password } });
}

describe('Commonflame account entry', () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
    window.history.replaceState({}, '', '/login');
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: false, signup: 'invite_only', setup_flow: 'token' });
  });
  it('shows platform-neutral account fields and invite-based account creation', async () => {
    render(<UserLogin onLogin={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'Welcome to your Commonflame' })).toBeInTheDocument();
    expect(screen.getByLabelText('Username')).toHaveAttribute('autoComplete', 'username');
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
    expect(screen.getByRole('link', { name: /connect an agent/i })).toHaveAttribute('href', '/auth.md');
    expect(await screen.findByRole('link', { name: 'Create an account' })).toHaveAttribute('href', '/signup');
    expect(screen.queryByText(/local account/i)).not.toBeInTheDocument();
  });
  it('signs in only after a successful backend response and clears the password', async () => {
    loginLocal.mockResolvedValue({ access_token: 'session', user: { username: 'owner' } });
    const onLogin = vi.fn(); render(<UserLogin onLogin={onLogin} />);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: ' owner ' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: 'Enter workspace' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith('session', 'owner'));
    expect(loginLocal).toHaveBeenCalledWith('owner', password);
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });
  it('keeps a failed sign-in on the form', async () => {
    loginLocal.mockRejectedValue(new Error('Invalid username or password.'));
    const onLogin = vi.fn(); render(<UserLogin onLogin={onLogin} />);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'owner' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enter workspace' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password.');
    expect(onLogin).not.toHaveBeenCalled();
  });
  it('carries a pending approval between sign-in and account creation without approving it', async () => {
    const next = '/oauth/authorize?client_id=agent&response_type=code&state=synthetic-state';
    window.history.replaceState({}, '', `/login?${new URLSearchParams({ next })}`);
    render(<UserLogin onLogin={vi.fn()} />);
    expect(screen.getByText(/You decide whether to approve it/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue to connection' })).toBeInTheDocument();
    const href = (await screen.findByRole('link', { name: 'Create an account' })).getAttribute('href')!;
    expect(new URL(href, window.location.origin).searchParams.get('next')).toBe(next);
    expect(sessionStorage.length).toBe(0);
  });
  it('discards an external approval return target', async () => {
    window.history.replaceState({}, '', '/login?next=https%3A%2F%2Fevil.example%2Fdevice');
    render(<UserLogin onLogin={vi.fn()} />);
    expect(await screen.findByRole('link', { name: 'Create an account' })).toHaveAttribute('href', '/signup');
    expect(screen.queryByText(/You decide whether to approve it/)).not.toBeInTheDocument();
  });
  it('opens owner onboarding automatically when the backend says setup is needed', async () => {
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: true, signup: 'invite_only', setup_flow: 'token' });
    render(<UserLogin onLogin={vi.fn()} />);
    expect(await screen.findByRole('heading', { name: 'Create the owner account' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set up Commonflame' })).toBeInTheDocument();
  });
  it('consumes a pasted owner setup token and clears sensitive fields on success', async () => {
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: true, signup: 'invite_only', setup_flow: 'token' });
    createAccount.mockResolvedValue({ access_token: 'session', user: { username: 'new-owner' } });
    const onLogin = vi.fn(); render(<UserLogin initialMode="account" onLogin={onLogin} />);
    expect(await screen.findByRole('heading', { name: 'Create the owner account' })).toBeInTheDocument();
    enterAccount('Setup token');
    fireEvent.click(screen.getByRole('button', { name: 'Set up Commonflame' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith('session', 'new-owner'));
    expect(createAccount).toHaveBeenCalledWith({ token, username: 'new-owner', password }, 'setup');
    expect(screen.getByLabelText('Setup token')).toHaveValue('');
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(screen.getByLabelText('Confirm password')).toHaveValue('');
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(window.location.href).not.toContain(token);
  });
  it('creates an invited account, keeping the approval return path on the sign-in link', async () => {
    const next = '/device?user_code=TEST-CODE';
    window.history.replaceState({}, '', `/signup?${new URLSearchParams({ next })}`);
    createAccount.mockResolvedValue({ access_token: 'session', user: { username: 'new-owner' } });
    const onLogin = vi.fn(); render(<UserLogin initialMode="account" onLogin={onLogin} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled());
    enterAccount('Invitation token');
    fireEvent.change(screen.getByLabelText(/Your name/), { target: { value: ' New Person ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalled());
    expect(createAccount).toHaveBeenCalledWith({ token, username: 'new-owner', password, full_name: 'New Person' }, 'invite');
    expect(new URL(screen.getByRole('link', { name: 'Sign in' }).getAttribute('href')!, window.location.origin).searchParams.get('next')).toBe(next);
  });
  it('does not send a token when the password confirmation differs', async () => {
    render(<UserLogin initialMode="account" onLogin={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled());
    enterAccount('Invitation token');
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'different-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Your passwords do not match.');
    expect(createAccount).not.toHaveBeenCalled();
  });
  it('explains an expired invitation without creating a session', async () => {
    createAccount.mockRejectedValue(new Error('Setup or invitation token is invalid or expired'));
    const onLogin = vi.fn(); render(<UserLogin initialMode="account" onLogin={onLogin} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled());
    enterAccount('Invitation token');
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid or expired');
    expect(onLogin).not.toHaveBeenCalled();
  });
  it('keeps account creation closed when setup status cannot be checked', async () => {
    getAccountStatus.mockRejectedValue(new Error('unavailable'));
    render(<UserLogin initialMode="account" onLogin={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not check account setup');
    expect(screen.getByRole('button', { name: 'Create account' })).toBeDisabled();
    expect(createAccount).not.toHaveBeenCalled();
  });
  it('lets a localhost user create an account without a token', async () => {
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: false, setup_flow: 'browser', signup: 'open' });
    createAccount.mockResolvedValue({ access_token: 'session', user: { username: 'another' } });
    const onLogin = vi.fn();
    render(<UserLogin initialMode="account" onLogin={onLogin} currentUsername="existing-user" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled());
    expect(screen.queryByLabelText('Invitation token')).not.toBeInTheDocument();
    expect(screen.getByText(/Signed in as existing-user/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'another' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith('session', 'another'));
    expect(createAccount).toHaveBeenCalledWith({ username: 'another', password }, 'signup');
  });
  it('opens token-free first-run setup from the ordinary login page', async () => {
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: true, setup_flow: 'browser', signup: 'open' });
    createAccount.mockResolvedValue({ access_token: 'session', user: { username: 'owner' } });
    render(<UserLogin onLogin={vi.fn()} />);
    expect(await screen.findByRole('heading', { name: 'Create the owner account' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Setup token')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'owner' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: 'Set up Commonflame' }));
    await waitFor(() => expect(createAccount).toHaveBeenCalledWith({ username: 'owner', password }, 'setup'));
  });
  it('makes joining someone else’s workspace optional during open signup', async () => {
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: false, setup_flow: 'browser', signup: 'open' });
    render(<UserLogin initialMode="account" onLogin={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Have an invitation to a shared workspace?' }));
    expect(screen.getByLabelText('Invitation token')).toBeRequired();
    fireEvent.change(screen.getByLabelText('Invitation token'), { target: { value: token } });
    fireEvent.click(screen.getByRole('button', { name: 'Create a private workspace instead' }));
    expect(screen.queryByLabelText('Invitation token')).not.toBeInTheDocument();
  });
  it('explains closed registration without offering a working signup form', async () => {
    getAccountStatus.mockResolvedValue({ auth_mode: 'builtin', setup_required: false, setup_flow: 'token', signup: 'closed' });
    render(<UserLogin initialMode="account" onLogin={vi.fn()} />);
    expect(await screen.findByText('New accounts are disabled on this installation.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create account' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });
});
