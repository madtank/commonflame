import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { storage } from '@/lib/storage';
import { logoutLocal } from '@/lib/local-auth';

export interface AuthUser { username: string; email: string; attributes?: Record<string, any>; }
interface AuthContextType {
  user: AuthUser | null; token: string | null; isLoading: boolean;
  isAuthenticated: boolean; error: string | null; clearError: () => void;
  signIn: (token: string, username: string) => void; signOut: () => Promise<void>;
}
const AuthContext = createContext<AuthContextType | null>(null);
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within an AuthProvider');
  return context;
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const signIn = (value: string, username: string) => {
    storage.setUserToken(value); storage.setUsername(username);
    const metadata = storage.getUserMetadata();
    setToken(value); setUser({ username, email: metadata?.email || '', attributes: metadata });
    setError(null);
  };
  useEffect(() => {
    let mounted = true;
    const readProfile = async () => {
      const accessToken = storage.getUserToken();
      if (!accessToken) return false;
      try {
        const response = await fetch('/auth/me', { headers: { Authorization: `Bearer ${accessToken}` }, credentials: 'include' });
        if (!response.ok) return false;
        const profile = await response.json();
        if (!profile.username) return false;
        storage.setUserMetadata(profile); storage.setUsername(profile.username);
        if (mounted) { setToken(accessToken); setUser({ username: profile.username, email: profile.email || '', attributes: profile }); }
        return true;
      } catch { return false; }
    };
    const restore = async () => {
      if (!await readProfile()) {
        const refreshed = await storage.refreshTokens();
        if (!refreshed || !await readProfile()) {
          storage.clearTokens();
          if (mounted) { setToken(null); setUser(null); }
        }
      }
      if (mounted) setIsLoading(false);
    };
    const refreshed = () => {
      const next = storage.getUserToken(); const metadata = storage.getUserMetadata();
      if (next && metadata?.username) {
        setToken(next); setUser({ username: metadata.username, email: metadata.email || '', attributes: metadata });
      }
    };
    const loggedOut = () => { setToken(null); setUser(null); };
    window.addEventListener('auth:token-refreshed', refreshed);
    window.addEventListener('auth:logout', loggedOut);
    void restore();
    return () => { mounted = false; window.removeEventListener('auth:token-refreshed', refreshed); window.removeEventListener('auth:logout', loggedOut); };
  }, []);
  const signOut = async () => {
    await logoutLocal();
    setToken(null); setUser(null);
  };
  return <AuthContext.Provider value={{ user, token, isLoading, isAuthenticated: Boolean(user && token), error, clearError: () => setError(null), signIn, signOut }}>{children}</AuthContext.Provider>;
}
