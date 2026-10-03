import { accountEntryHref } from './approval-navigation';

export async function redirectToSignIn(next: string | null = null): Promise<void> {
  window.location.assign(accountEntryHref('/login', next));
}
