import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Toaster } from '@/components/ui/toaster';
import { toast } from '@/components/ui/use-toast';
import { UserLogin } from '@/components/UserLogin';
import { useAuth } from '@/contexts/AuthContext';
import { useTokenRefresh } from '@/hooks/useTokenRefresh';
import { applyThemePreference, getStoredThemeState } from '@/lib/theme';
import { storage } from '@/lib/storage';

const Workspace = lazy(() => import('@/pages/AxPlatformPage'));
const DeviceVerify = lazy(() => import('@/pages/DeviceVerifyPage'));
const Admin = lazy(() => import('@/pages/ModernAdminPage'));
function Loading() {
  return <div className="flex min-h-screen items-center justify-center bg-background text-muted-foreground" role="status">Opening Waystation…</div>;
}

export default function App() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [path, setPath] = useState(window.location.pathname);
  useTokenRefresh();
  const lastIdentity = useRef<string | null>(null);
  useEffect(() => {
    const identity = auth.user?.attributes?.id || auth.user?.username || null;
    if (identity !== lastIdentity.current) queryClient.clear();
    lastIdentity.current = identity;
  }, [auth.user?.attributes?.id, auth.user?.username, queryClient]);
  useEffect(() => {
    applyThemePreference(getStoredThemeState().theme);
    const changed = () => setPath(window.location.pathname);
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  const onLogin = (token: string, username: string) => {
    queryClient.clear();
    auth.signIn(token, username);
    const pending = sessionStorage.getItem('ax_device_verify_redirect');
    const target = pending && /^\/(?:auth|oauth)\/device\/verify(?:\?|$)/.test(pending) ? pending : '/app';
    sessionStorage.removeItem('ax_device_verify_redirect');
    window.history.replaceState({}, '', target);
    setPath(window.location.pathname);
  };
  const onLogout = async () => {
    try {
      await auth.signOut();
      queryClient.clear();
      localStorage.removeItem('waystation_query_cache');
      window.history.replaceState({}, '', '/login'); setPath('/login');
    } catch (error) {
      toast({ title: 'Sign out failed', description: error instanceof Error ? error.message : 'Please retry.', variant: 'destructive' });
    }
  };
  let page;
  if (auth.isLoading) page = <Loading />;
  else if (/^\/(auth|oauth)\/device\/verify/.test(path)) page = <DeviceVerify userToken={auth.token} username={auth.user?.username || null} />;
  else if (!auth.isAuthenticated) page = <UserLogin onLogin={onLogin} />;
  else if (path === '/admin') {
    const admin = ['admin', 'super_admin'].includes(auth.user?.attributes?.role);
    page = <Admin username={auth.user?.username} onLogout={onLogout} isAdminUser={admin} isAdminValidated={true} />;
  } else page = <Workspace spaceName={storage.getSpace()?.name} username={auth.user?.username} onLogout={onLogout} />;
  return <ErrorBoundary><Suspense fallback={<Loading />}>{page}</Suspense><Toaster /></ErrorBoundary>;
}
