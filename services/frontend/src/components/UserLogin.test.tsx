import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UserLogin } from './UserLogin';
const { loginLocal } = vi.hoisted(() => ({ loginLocal: vi.fn() }));
vi.mock('@/lib/local-auth', () => ({ loginLocal }));

describe('Waystation login', () => {
  beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });
  it('shows local account fields and the first-run command', () => {
    render(<UserLogin onLogin={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'Welcome to your Waystation' })).toBeInTheDocument();
    expect(screen.getByLabelText('Username')).toHaveAttribute('autoComplete', 'username');
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
    expect(screen.getByRole('link', { name: /connect an agent/i })).toHaveAttribute('href', '/auth.md');
    expect(screen.getByText('docker compose exec backend python -m scripts.create_local_user')).toBeInTheDocument();
  });
  it('signs in only after a successful backend response and clears the password', async () => {
    loginLocal.mockResolvedValue({ access_token: 'session', user: { username: 'owner' } });
    const onLogin = vi.fn(); render(<UserLogin onLogin={onLogin} />);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: ' owner ' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a-unique-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enter workspace' }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith('session', 'owner'));
    expect(loginLocal).toHaveBeenCalledWith('owner', 'a-unique-password');
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });
  it('keeps the user on the form and explains a failed login', async () => {
    loginLocal.mockRejectedValue(new Error('Invalid username or password.'));
    const onLogin = vi.fn(); render(<UserLogin onLogin={onLogin} />);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'owner' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enter workspace' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password.');
    expect(onLogin).not.toHaveBeenCalled();
  });
});
