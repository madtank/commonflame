import { FormEvent, useState } from 'react';
import { ArrowRight, Loader2, Moon, Sun } from 'lucide-react';
import { Logo } from './Logo';
import { loginLocal } from '@/lib/local-auth';
import { applyThemePreference, getStoredThemeState } from '@/lib/theme';

export function UserLogin({ onLogin }: { onLogin: (token: string, username: string) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [isDark, setIsDark] = useState(() => getStoredThemeState().isDarkMode);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      const session = await loginLocal(username.trim(), password);
      setPassword('');
      onLogin(session.access_token, session.user.username);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The server is unavailable. Please try again.');
    } finally { setBusy(false); }
  };
  const toggleTheme = () => setIsDark(applyThemePreference(isDark ? 'light' : 'dark').isDarkMode);
  return (
    <div className={`min-h-screen ${isDark ? 'bg-[#080f1a] text-slate-100' : 'bg-slate-50 text-slate-900'}`}>
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <a href="/" aria-label="Waystation home"><Logo /></a>
        <button onClick={toggleTheme} className="rounded-full border border-current/15 p-2.5" aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}>
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </header>
      <main className="mx-auto grid max-w-6xl gap-12 px-6 pb-16 pt-12 md:grid-cols-[1.1fr_1fr] md:items-center md:pt-24">
        <section>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-500">Your agents. Shared ground.</p>
          <h1 className="mt-5 max-w-xl text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">Good work starts with a place to gather.</h1>
          <p className="mt-6 max-w-lg text-lg leading-relaxed opacity-70">Bring agents, people, tasks, and context into one workspace you run yourself.</p>
          <div className="mt-9 flex flex-wrap gap-3 text-sm">
            {['Durable conversations', 'Shared tasks', 'MCP tools'].map(label => <span key={label} className={`rounded-full border px-4 py-2 ${isDark ? 'border-slate-700' : 'border-slate-300'}`}>{label}</span>)}
          </div>
          <a href="/auth.md" className="mt-10 inline-flex items-center gap-2 text-sm font-medium text-cyan-500 hover:underline">Connect an agent <ArrowRight className="h-4 w-4" /></a>
        </section>
        <section className={`rounded-3xl border p-7 sm:p-9 ${isDark ? 'border-slate-700/70 bg-slate-900/80 shadow-2xl' : 'border-slate-200 bg-white shadow-xl shadow-slate-200/50'}`}>
          <h2 className="text-2xl font-semibold tracking-tight">Welcome to your Waystation</h2>
          <p className="mt-2 text-sm opacity-65">Sign in to this installation.</p>
          <form onSubmit={submit} className="mt-7 space-y-5">
            <div><label htmlFor="username" className="mb-2 block text-sm font-medium">Username</label>
              <input id="username" name="username" autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} className={`w-full rounded-xl border px-3 py-3 outline-none focus:ring-2 focus:ring-cyan-500 ${isDark ? 'border-slate-700 bg-slate-950' : 'border-slate-300 bg-white'}`} /></div>
            <div><label htmlFor="password" className="mb-2 block text-sm font-medium">Password</label>
              <input id="password" name="password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} className={`w-full rounded-xl border px-3 py-3 outline-none focus:ring-2 focus:ring-cyan-500 ${isDark ? 'border-slate-700 bg-slate-950' : 'border-slate-300 bg-white'}`} /></div>
            {error && <p role="alert" className="rounded-xl border border-red-400/40 bg-red-400/10 px-3 py-3 text-sm text-red-500">{error}</p>}
            <button disabled={busy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-60">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} {busy ? 'Signing in…' : 'Enter workspace'}</button>
          </form>
          <details className="mt-7 border-t border-current/10 pt-5 text-sm">
            <summary className="cursor-pointer font-medium opacity-75">Setting up for the first time?</summary>
            <p className="mt-3 leading-relaxed opacity-70">The person running this installation creates local accounts. From the repository directory, run:</p>
            <code className={`mt-3 block overflow-x-auto rounded-lg p-3 text-xs ${isDark ? 'bg-slate-950' : 'bg-slate-100'}`}>docker compose exec backend python -m scripts.create_local_user</code>
            <p className="mt-3 text-xs leading-relaxed opacity-60">Choose a unique password in the terminal, then sign in here.</p>
          </details>
        </section>
      </main>
      <footer className="mx-auto max-w-6xl px-6 pb-7 text-xs opacity-50">Waystation · Self-hosted agent collaboration</footer>
    </div>
  );
}
