/**
 * Robust clipboard utility with multiple fallback strategies
 * Handles permissions issues, browser compatibility, and secure contexts
 */

/**
 * Attempts to copy text to clipboard using multiple strategies
 * @param text - Text to copy to clipboard
 * @returns Promise<boolean> - Success status
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // Strategy 1: Modern Clipboard API (requires secure context and permissions)
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      console.warn('Clipboard API failed, trying fallback:', err);
    }
  }

  // Strategy 2: Legacy execCommand (works in more contexts)
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    textArea.style.opacity = '0';
    textArea.style.pointerEvents = 'none';

    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();

    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);

    if (successful) {
      return true;
    }
  } catch (err) {
    console.warn('execCommand fallback failed:', err);
  }

  // Strategy 3: Manual selection (last resort)
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '50%';
    textArea.style.top = '50%';
    textArea.style.transform = 'translate(-50%, -50%)';
    textArea.style.zIndex = '9999';
    textArea.style.padding = '10px';
    textArea.style.border = '1px solid #ccc';
    textArea.style.borderRadius = '4px';
    textArea.style.backgroundColor = 'white';
    textArea.style.color = 'black';

    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();

    // Let user manually copy
    setTimeout(() => {
      if (document.body.contains(textArea)) {
        document.body.removeChild(textArea);
      }
    }, 5000);

    return false; // User needs to manually copy
  } catch (err) {
    console.error('All clipboard strategies failed:', err);
    return false;
  }
}

/**
 * Gets an appropriate error message based on the browser and context
 */
export function getClipboardErrorMessage(): string {
  if (!navigator.clipboard) {
    return 'Clipboard API not supported. Please copy manually.';
  }

  if (!window.isSecureContext) {
    return 'Clipboard access requires a secure connection (HTTPS).';
  }

  return 'Clipboard access blocked. Please grant permission or copy manually.';
}

/**
 * Checks if clipboard API is likely to work
 */
export function isClipboardSupported(): boolean {
  return !!(navigator.clipboard && window.isSecureContext);
}
