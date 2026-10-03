import { useEffect, useState } from 'react';
import { canInviteToWorkspace, createWorkspaceInvitation, type WorkspaceInvitation as Invitation } from '@/lib/local-auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

/** The one-time token lives only in this open settings panel. */
export function WorkspaceInvitation() {
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [allowed, setAllowed] = useState(false);
  useEffect(() => {
    let active = true;
    canInviteToWorkspace().then(value => { if (active) setAllowed(value); }).catch(() => {});
    return () => { active = false; };
  }, []);
  const create = async () => {
    setBusy(true); setError('');
    try { setInvitation(await createWorkspaceInvitation()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not create an invitation. Please retry.'); }
    finally { setBusy(false); }
  };
  const copy = async () => {
    if (!invitation) return;
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(invitation.token);
      setCopied(true);
    } catch { setError('Could not copy the invitation. Select the token and copy it yourself.'); }
  };
  if (!allowed) return null;
  return <Card className="border-border bg-card shadow-none">
    <CardHeader className="pb-3"><CardTitle className="text-lg text-foreground">Invite someone to this workspace</CardTitle></CardHeader>
    <CardContent className="space-y-4 text-sm">
      <p className="text-muted-foreground">An invitation lets one person create an account and join your current workspace. They can then approve connections for their own agents.</p>
      {invitation ? <>
        <p>Give them <a className="text-cyan-500 hover:underline" href="/signup">{window.location.origin}/signup</a> and the token below. Keep the token private.</p>
        <div className="space-y-2"><Label htmlFor="workspace-invitation-token">Invitation token</Label><Input id="workspace-invitation-token" readOnly autoComplete="off" data-1p-ignore data-lpignore="true" value={invitation.token} className="font-mono" /></div>
        <p className="text-xs text-muted-foreground">Expires {new Date(invitation.expires_at).toLocaleString()}. This token is shown only while this panel is open.</p>
        <Button type="button" variant="outline" onClick={() => void copy()}>{copied ? 'Copied' : 'Copy invitation token'}</Button>
      </> : <Button type="button" disabled={busy} onClick={() => void create()}>{busy ? 'Creating invitation…' : 'Create invitation'}</Button>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
    </CardContent>
  </Card>;
}
