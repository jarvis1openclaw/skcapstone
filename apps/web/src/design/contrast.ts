/**
 * WCAG 2.x relative luminance and contrast ratio helpers.
 *
 * Used by design-token tests to prove every declared text-on-surface
 * pair meets WCAG AA before the tokens ship in the shell.
 */

function channelToLinear(channel: number): number {
  const srgb = channel / 255;
  return srgb <= 0.04045 ? srgb / 12.92 : Math.pow((srgb + 0.055) / 1.055, 2.4);
}

export function parseHexColor(hex: string): {
  r: number;
  g: number;
  b: number;
} {
  const match = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (match === null || match[1] === undefined) {
    throw new Error(`invalid hex color: ${hex}`);
  }
  const value = match[1];
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

export function relativeLuminance(hex: string): number {
  const { r, g, b } = parseHexColor(hex);
  return (
    0.2126 * channelToLinear(r) +
    0.7152 * channelToLinear(g) +
    0.0722 * channelToLinear(b)
  );
}

export function contrastRatio(foreground: string, background: string): number {
  const first = relativeLuminance(foreground);
  const second = relativeLuminance(background);
  const lighter = Math.max(first, second);
  const darker = Math.min(first, second);
  return (lighter + 0.05) / (darker + 0.05);
}

/** WCAG AA minimum ratios: 4.5 for normal text, 3.0 for large text. */
export const WCAG_AA_NORMAL_TEXT = 4.5;
export const WCAG_AA_LARGE_TEXT = 3.0;
