import { describe, expect, it } from 'vitest';
import { config } from './environment';
describe('portable browser configuration', () => {
  it('uses only the current installation and carries no cloud auth defaults', () => {
    expect(config.apiUrl).toBe('');
    expect(config.directApiUrl).toBe(window.location.origin);
    expect(config.mcpUrl).toBe(window.location.origin);
    expect(Object.keys(config)).toEqual(['apiUrl', 'directApiUrl', 'mcpUrl', 'wsUrl', 'environment', 'features']);
  });
});
