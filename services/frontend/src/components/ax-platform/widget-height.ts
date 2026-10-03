export const WIDGET_HEIGHT_TRANSITION_GROW = "height 45ms ease-out";
export const WIDGET_HEIGHT_TRANSITION_SHRINK = "height 0ms linear";

export function getWidgetHeightTransition(
  previousHeight: number,
  nextHeight: number,
) {
  return nextHeight < previousHeight
    ? WIDGET_HEIGHT_TRANSITION_SHRINK
    : WIDGET_HEIGHT_TRANSITION_GROW;
}
