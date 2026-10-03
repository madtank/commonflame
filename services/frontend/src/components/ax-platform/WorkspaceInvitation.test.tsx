import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkspaceInvitation } from './WorkspaceInvitation';
const { canInviteToWorkspace, createWorkspaceInvitation } = vi.hoisted(() => ({ canInviteToWorkspace: vi.fn(), createWorkspaceInvitation: vi.fn() }));
vi.mock('@/lib/local-auth', () => ({ canInviteToWorkspace, createWorkspaceInvitation }));

describe('workspace invitations', () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
    canInviteToWorkspace.mockResolvedValue(true);
    createWorkspaceInvitation.mockResolvedValue({ token: 'synthetic-invite-token', expires_at: '2030-01-01T00:00:00Z' });
  });
  it('shows the invitation action only with authoritative workspace-admin permission', async () => {
    canInviteToWorkspace.mockResolvedValue(false);
    render(<WorkspaceInvitation />);
    await waitFor(() => expect(canInviteToWorkspace).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Create invitation' })).not.toBeInTheDocument();
    expect(createWorkspaceInvitation).not.toHaveBeenCalled();
  });
  it('shows a one-time token only after a deliberate request, keeping it out of URLs and storage', async () => {
    render(<WorkspaceInvitation />);
    const createButton = await screen.findByRole('button', { name: 'Create invitation' });
    expect(createWorkspaceInvitation).not.toHaveBeenCalled();
    fireEvent.click(createButton);
    expect(await screen.findByLabelText('Invitation token')).toHaveValue('synthetic-invite-token');
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/signup');
    expect(link.getAttribute('href')).not.toContain('synthetic-invite-token');
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it('forgets the one-time token when the settings panel is closed', async () => {
    const first = render(<WorkspaceInvitation />);
    fireEvent.click(await screen.findByRole('button', { name: 'Create invitation' }));
    await screen.findByLabelText('Invitation token');
    first.unmount();
    render(<WorkspaceInvitation />);
    await screen.findByRole('button', { name: 'Create invitation' });
    expect(screen.queryByLabelText('Invitation token')).not.toBeInTheDocument();
  });
  it('explains revoked admin permission without exposing a token', async () => {
    createWorkspaceInvitation.mockRejectedValue(new Error('Workspace admin permission required'));
    render(<WorkspaceInvitation />);
    fireEvent.click(await screen.findByRole('button', { name: 'Create invitation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Workspace admin permission required');
    expect(screen.queryByLabelText('Invitation token')).not.toBeInTheDocument();
  });
});
