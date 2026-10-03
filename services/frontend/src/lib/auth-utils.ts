/** Unauthenticated actions use the local sign-in page. */
export async function redirectToLocalLogin(): Promise<void> {
  window.location.assign('/login');
}
export function getGitHubAuthUrl(): string {
  return '/login';
}
