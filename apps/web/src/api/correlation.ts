/**
 * Correlation identifiers for request traceability.
 *
 * Every API request carries an X-Correlation-ID so support and audit can
 * trace a browser action across the API, policy gateway, and audit log.
 * Error panels surface the identifier so a user can quote it without
 * exposing any protected record detail.
 */

const CORRELATION_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function newCorrelationId(): string {
  return crypto.randomUUID();
}

export function isCorrelationId(value: string): boolean {
  return CORRELATION_ID_PATTERN.test(value);
}
