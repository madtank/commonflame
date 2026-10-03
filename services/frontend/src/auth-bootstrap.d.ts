export {};

declare global {
  interface Window {
    __AUTH_BOOTSTRAP__?: {
      token: string | null;
      username: string | null;
    };
  }
}
