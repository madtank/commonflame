// Browser traffic stays on the deployment's origin. Vite/nginx routes requests
// to the private backend and MCP service, so no cloud account is needed.
const ENVIRONMENT = import.meta.env.VITE_ENVIRONMENT || 'production';
const origin = typeof window !== 'undefined' ? window.location.origin : '';
export const config = {
  apiUrl: '',
  directApiUrl: origin,
  mcpUrl: origin,
  wsUrl: origin.replace(/^http/, 'ws'),
  environment: ENVIRONMENT,
  features: {
    debugMode: import.meta.env.DEV,
    mockData: ENVIRONMENT === 'test',
    verboseLogging: import.meta.env.DEV,
    welcomeDialogEnabled: false,
  },
};
export default config;
