import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Logo } from './Logo';
describe('Waystation logo', () => {
  it('names the workspace and its purpose', () => {
    render(<Logo />);
    expect(screen.getByText('Waystation')).toBeInTheDocument();
    expect(screen.getByText('A place for agents to work together')).toBeInTheDocument();
  });
});
