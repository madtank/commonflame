import { describe, expect, it } from "vitest";
import {
  getWidgetHeightTransition,
  WIDGET_HEIGHT_TRANSITION_GROW,
  WIDGET_HEIGHT_TRANSITION_SHRINK,
} from "./widget-height";

describe("widget-height", () => {
  it("keeps gentle easing when a widget grows", () => {
    expect(getWidgetHeightTransition(240, 420)).toBe(
      WIDGET_HEIGHT_TRANSITION_GROW,
    );
  });

  it("snaps immediately when a widget shrinks", () => {
    expect(getWidgetHeightTransition(420, 240)).toBe(
      WIDGET_HEIGHT_TRANSITION_SHRINK,
    );
  });

  it("uses the non-destructive transition when height stays flat", () => {
    expect(getWidgetHeightTransition(240, 240)).toBe(
      WIDGET_HEIGHT_TRANSITION_GROW,
    );
  });
});
