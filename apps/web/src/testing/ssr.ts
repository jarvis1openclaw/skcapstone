/**
 * Server-side render helpers for component tests. The shell runs vitest
 * in a node environment, so component markup is asserted through
 * renderToStaticMarkup rather than a DOM renderer.
 */

import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const UUID_PATTERN =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g;

export function renderStatic(node: ReactElement): string {
  return renderToStaticMarkup(node);
}

/** Replace random correlation ids so snapshots stay deterministic. */
export function stabilize(markup: string): string {
  return markup.replace(UUID_PATTERN, "00000000-0000-4000-8000-000000000000");
}
