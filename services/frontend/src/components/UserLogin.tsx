import { FormEvent, useEffect, useState } from 'react';
import { ArrowRight, Loader2, Moon, Sun } from 'lucide-react';
import { Logo } from './Logo';
import { createAccount, getAccountStatus, loginLocal, type AccountStatus } from '@/lib/local-auth';
import { accountEntryHref, getApprovalReturnPath } from '@/lib/approval-navigation';
import { applyThemePreference, getStoredThemeState } from '@/lib/theme';

type UserLoginProps = {
  onLogin: (token: string, username: string) => void;
  initialMode?: 'login' | 'account';
  currentUsername?: string;
};

export function UserLogin({ onLogin, initialMode = 'login', currentUsername }: UserLoginProps) {
  const next = getApprovalReturnPath();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [fullName, setFullName] = useState('');
  const [accountToken, setAccountToken] = useState('');
  const [joiningWorkspace, setJoiningWorkspace] = useState(false);
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [statusAttempt, setStatusAttempt] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [isDark, setIsDark] = useState(() => getStoredThemeState().isDarkMode);
  useEffect(() => {
    let active = true;
    setStatusError('');
    getAccountStatus().then(value => { if (active) setStatus(value); })
      .catch(() => { if (active) setStatusError('Could not check account setup. Please try again.'); });
    return () => { active = false; };
  }, [statusAttempt]);
  const ownerSetup = status?.setup_required === true;
  const creatingAccount = initialMode === 'account' || ownerSetup;
  const registrationClosed = !ownerSetup && status?.signup === 'closed';
  const needsToken = creatingAccount && (ownerSetup ? status?.setup_flow === 'token'
    : status?.signup === 'invite_only' || joiningWorkspace);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (creatingAccount && (!status || password !== confirmation)) {
      setError(status ? 'Your passwords do not match.' : 'Wait for account setup to finish loading.');
      return;
    }
    setBusy(true);
    try {
      const session = creatingAccount
        ? await createAccount({ ...(needsToken ? { token: accountToken.trim() } : {}), username: username.trim(), password, ...(fullName.trim() ? { full_name: fullName.trim() } : {}) }, ownerSetup ? 'setup' : needsToken ? 'invite' : 'signup')
        : await loginLocal(username.trim(), password);
      setPassword(''); setConfirmation(''); setAccountToken('');
      onLogin(session.access_token, session.user.username);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Commonflame is unavailable. Please try again.');
    } finally { setBusy(false); }
  };
  const toggleTheme = () => setIsDark(applyThemePreference(isDark ? 'light' : 'dark').isDarkMode);
  const inputClass = `w-full rounded-xl border px-3 py-3 outline-none focus:ring-2 focus:ring-cyan-500 ${isDark ? 'border-slate-700 bg-slate-950' : 'border-slate-300 bg-white'}`;
  const heading = creatingAccount ? (ownerSetup ? 'Create the owner account' : 'Create your account') : 'Welcome to your Commonflame';
  return (
    <div className={`min-h-screen ${isDark ? 'bg-[#080f1a] text-slate-100' : 'bg-slate-50 text-slate-900'}`}>
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <a href="/" aria-label="Commonflame home"><Logo /></a>
        <button onClick={toggleTheme} className="rounded-full border border-current/15 p-2.5" aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}>
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </header>
      <main className="mx-auto grid max-w-6xl gap-12 px-6 pb-16 pt-12 md:grid-cols-[1.1fr_1fr] md:items-center md:pt-24">
        <section>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-500">Your agents. Shared ground.</p>
          <h1 className="mt-5 max-w-xl text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">Good work starts with a place to gather.</h1>
          <p className="mt-6 max-w-lg text-lg leading-relaxed opacity-70">Bring agents, people, tasks, and context into one shared workspace.</p>
          <div className="mt-9 flex flex-wrap gap-3 text-sm">
            {['Durable conversations', 'Shared tasks', 'MCP tools'].map(label => <span key={label} className={`rounded-full border px-4 py-2 ${isDark ? 'border-slate-700' : 'border-slate-300'}`}>{label}</span>)}
          </div>
          <a href="/auth.md" className="mt-10 inline-flex items-center gap-2 text-sm font-medium text-cyan-500 hover:underline">Connect an agent <ArrowRight className="h-4 w-4" /></a>
        </section>
        <section className={`rounded-3xl border p-7 sm:p-9 ${isDark ? 'border-slate-700/70 bg-slate-900/80 shadow-2xl' : 'border-slate-200 bg-white shadow-xl shadow-slate-200/50'}`}>
          <h2 className="text-2xl font-semibold tracking-tight">{heading}</h2>
          <p className="mt-2 text-sm opacity-65">{creatingAccount
            ? ownerSetup ? needsToken ? 'Use the setup token from the person running this Commonflame.' : 'Choose your account details to finish setting up Commonflame.'
              : registrationClosed ? 'New accounts are disabled on this installation.'
              : needsToken ? 'Use your invitation to join a shared workspace.' : 'Choose a username and password. Your private workspace is created automatically.'
            : next ? 'Sign in to review the agent connection.' : 'Sign in with your Commonflame account.'}</p>
          {currentUsername && <p className="mt-4 rounded-xl border border-cyan-500/25 bg-cyan-500/10 p-3 text-sm">Signed in as {currentUsername}. <a href="/app" className="font-medium text-cyan-500 hover:underline">Return to workspace</a></p>}
          {ownerSetup && !needsToken && <p className="mt-4 rounded-xl border border-cyan-500/25 bg-cyan-500/10 p-3 text-sm">First run · This account will administer your first workspace. Setup closes once the account is created.</p>}
          {next && <p className="mt-4 rounded-xl border border-cyan-500/25 bg-cyan-500/10 p-3 text-sm">After {creatingAccount ? 'creating your account' : 'signing in'}, you’ll return to review the connection. You decide whether to approve it.</p>}
          {creatingAccount && !status && !statusError && <p className="mt-5 text-sm opacity-65" role="status">Checking account setup…</p>}
          {statusError && <div className="mt-5 text-sm"><p role="alert">{statusError}</p><button type="button" className="mt-2 text-cyan-500 hover:underline" onClick={() => setStatusAttempt(value => value + 1)}>Try again</button></div>}
          {!(creatingAccount && registrationClosed) && <form onSubmit={submit} className="mt-7 space-y-5">
            <fieldset disabled={busy || (creatingAccount && !status)} className="space-y-5 disabled:opacity-60">
              {creatingAccount && <>
                {needsToken && <>
                <div><label htmlFor="account-token" className="mb-2 block text-sm font-medium">{ownerSetup ? 'Setup token' : 'Invitation token'}</label>
                  <input id="account-token" name="account-token" type="password" autoComplete="off" spellCheck={false} required minLength={16} maxLength={256} value={accountToken} onChange={event => setAccountToken(event.target.value)} className={inputClass} aria-describedby="token-help" />
                  <p id="token-help" className="mt-2 text-xs opacity-65">Paste your token here. It works once; keep it private.</p></div>
                </>}
                <div><label htmlFor="full-name" className="mb-2 block text-sm font-medium">Your name <span className="font-normal opacity-65">(optional)</span></label>
                  <input id="full-name" name="full-name" autoComplete="name" maxLength={100} value={fullName} onChange={event => setFullName(event.target.value)} className={inputClass} /></div>
              </>}
              <div><label htmlFor="username" className="mb-2 block text-sm font-medium">Username</label>
                <input id="username" name="username" autoComplete="username" required minLength={creatingAccount ? 3 : undefined} maxLength={creatingAccount ? 50 : 255} pattern={creatingAccount ? '[A-Za-z0-9][A-Za-z0-9_.\\-]+' : undefined} value={username} onChange={event => setUsername(event.target.value)} className={inputClass} />
                {creatingAccount && <p className="mt-2 text-xs opacity-65">3–50 characters. Start with a letter or number; use letters, numbers, dots, dashes, or underscores.</p>}</div>
              <div><label htmlFor="password" className="mb-2 block text-sm font-medium">Password</label>
                <input id="password" name="password" type="password" autoComplete={creatingAccount ? 'new-password' : 'current-password'} required minLength={creatingAccount ? 15 : undefined} maxLength={512} value={password} onChange={event => setPassword(event.target.value)} className={inputClass} />
                {creatingAccount && <p className="mt-2 text-xs opacity-65">Use at least 15 characters.</p>}</div>
              {creatingAccount && <div><label htmlFor="confirm-password" className="mb-2 block text-sm font-medium">Confirm password</label>
                <input id="confirm-password" name="confirm-password" type="password" autoComplete="new-password" required minLength={15} maxLength={512} value={confirmation} onChange={event => setConfirmation(event.target.value)} className={inputClass} /></div>}
            </fieldset>
            {error && <p role="alert" className="rounded-xl border border-red-400/40 bg-red-400/10 px-3 py-3 text-sm text-red-500">{error}</p>}
            <button disabled={busy || (creatingAccount && !status)} className="flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-60">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} {busy ? creatingAccount ? 'Creating account…' : 'Signing in…' : creatingAccount ? ownerSetup ? 'Set up Commonflame' : 'Create account' : next ? 'Continue to connection' : 'Enter workspace'}</button>
          </form>}
          <div className="mt-7 border-t border-current/10 pt-5 text-sm">
            {creatingAccount ? <p>Already have an account? <a href={accountEntryHref('/login', next)} className="font-medium text-cyan-500 hover:underline">Sign in</a></p>
              : !registrationClosed && <p>{status?.signup === 'open' ? 'New here?' : 'Have an invitation?'} <a href={accountEntryHref('/signup', next)} className="font-semibold text-cyan-500 hover:underline">Create an account</a></p>}
            {creatingAccount && !ownerSetup && status?.signup === 'open' && <button type="button" className="mt-3 text-sm text-cyan-500 hover:underline" onClick={() => { setJoiningWorkspace(value => !value); setAccountToken(''); setError(''); }}>{joiningWorkspace ? 'Create a private workspace instead' : 'Have an invitation to a shared workspace?'}</button>}
            {creatingAccount && needsToken && <p className="mt-3 text-xs leading-relaxed opacity-65">{ownerSetup ? 'The server operator can issue a setup token. See the setup instructions.' : 'Ask a workspace owner for an invitation to join their workspace.'}</p>}
          </div>
        </section>
      </main>
      <footer className="mx-auto max-w-6xl px-6 pb-7 text-xs opacity-50">Commonflame · A shared home for your agents</footer>
    </div>
  );
}
