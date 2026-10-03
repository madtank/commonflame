/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_COMMIT_SHA: string;
  readonly VITE_ENVIRONMENT: string;
  readonly VITE_COGNITO_DOMAIN: string;
  readonly VITE_COGNITO_CLIENT_ID: string;
  readonly VITE_COGNITO_USER_POOL_ID?: string;
  readonly VITE_COGNITO_REDIRECT_URI: string;
  readonly VITE_ENABLE_AGENT_M2M_CREDENTIALS?: string;
  readonly VITE_ENABLE_SUMMARY_CARDS?: string;
  readonly VITE_CONTACT_ENDPOINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
