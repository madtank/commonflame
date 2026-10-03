/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_COMMIT_SHA: string;
  readonly VITE_ENVIRONMENT: string;
  readonly VITE_ENABLE_AGENT_M2M_CREDENTIALS?: string;
  readonly VITE_ENABLE_SUMMARY_CARDS?: string;
  readonly VITE_CONTACT_ENDPOINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
