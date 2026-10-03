import React, { Component, ReactNode } from 'react';
import {
  isRecoverableDynamicImportError,
  maybeReloadForDynamicImportError,
} from '@/lib/dynamic-import-recovery';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    maybeReloadForDynamicImportError(error);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div className="p-6 bg-red-50 border border-red-200 rounded-lg">
            <h2 className="text-xl font-bold text-red-800 mb-4">Component Error</h2>
            <div className="space-y-2">
              <p className="text-red-700">
                <strong>Error:</strong> {this.state.error?.message || 'Unknown error'}
              </p>
              <p className="text-red-700">
                <strong>Stack:</strong>
              </p>
              <pre className="text-xs bg-red-100 p-2 rounded overflow-auto">
                {this.state.error?.stack || 'No stack trace'}
              </pre>
            </div>
            <button
              onClick={() => {
                if (!maybeReloadForDynamicImportError(this.state.error)) {
                  this.setState({ hasError: false, error: null });
                }
              }}
              className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
            >
              {isRecoverableDynamicImportError(this.state.error)
                ? 'Reload App'
                : 'Try Again'}
            </button>
          </div>
        )
      );
    }

    return this.props.children;
  }
}
