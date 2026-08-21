/**
 * Status badge. Status is always conveyed by text label plus a non-color
 * glyph in addition to color, so meaning never depends on color alone.
 */

import type { StatusPresentation } from "../design/tokens";
import { toneColors } from "../design/tokens";

export function StatusBadge(props: { status: StatusPresentation }) {
  const { status } = props;
  const tone = toneColors[status.tone];
  return (
    <span
      className="sl-status-badge"
      data-tone={status.tone}
      style={{
        color: tone.foreground,
        backgroundColor: tone.background,
        borderColor: tone.border,
      }}
    >
      <span aria-hidden="true" className="sl-status-glyph">
        {status.glyph}
      </span>
      <span>{status.label}</span>
    </span>
  );
}
