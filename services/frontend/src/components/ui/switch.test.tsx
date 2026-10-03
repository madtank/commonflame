import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen } from "@/test/utils";
import { Switch } from "./switch";

describe("Switch", () => {
  it("uses compact mobile sizing with the desktop size restored at sm and up", () => {
    render(
      <Switch
        checked={true}
        onCheckedChange={vi.fn()}
        aria-label="Compact switch"
      />,
    );

    const button = screen.getByRole("switch", { name: /compact switch/i });
    const thumb = button.querySelector("span");

    expect(button.className).toContain("h-5 w-9");
    expect(button.className).toContain("sm:h-6 sm:w-11");
    expect(thumb?.className).toContain("h-4 w-4");
    expect(thumb?.className).toContain("sm:h-5 sm:w-5");
    expect(thumb?.className).toContain("translate-x-4 sm:translate-x-5");
  });
});
