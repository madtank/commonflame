import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Logo } from './Logo';
describe('Commonflame logo', () => {
  it('names the workspace and its purpose', () => {
    render(<Logo />);
    expect(screen.getByText('Commonflame')).toBeInTheDocument();
    expect(screen.getByText('A shared workspace for people and agents')).toBeInTheDocument();
  });
});
