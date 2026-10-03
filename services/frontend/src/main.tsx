import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { AuthProvider } from './contexts/AuthContext';
import { markDynamicImportRecoveryLoadSucceeded } from './lib/dynamic-import-recovery';
import './index.css';

markDynamicImportRecoveryLoadSucceeded();
const queryClient = new QueryClient({ defaultOptions: {
  queries: { staleTime: 1000 * 60 * 5, gcTime: 1000 * 60 * 10, retry: 1, refetchOnWindowFocus: false },
} });
// Workspace data stays in memory and is cleared on identity changes.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={queryClient}><BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter></QueryClientProvider></React.StrictMode>,
);
