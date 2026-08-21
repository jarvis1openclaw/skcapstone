/**
 * Fail-closed API error mapping.
 *
 * The shell renders protected error states from these kinds. Unknown or
 * unexpected responses map to "unknown" and render a generic unavailable
 * panel; the shell never guesses at protected detail from an error.
 */

export type ApiErrorKind =
  "unauthenticated" | "forbidden" | "not_found" | "unavailable" | "unknown";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly correlationId: string;

  constructor(options: {
    kind: ApiErrorKind;
    status: number | null;
    correlationId: string;
  }) {
    super(`request failed (${options.kind})`);
    this.name = "ApiError";
    this.kind = options.kind;
    this.status = options.status;
    this.correlationId = options.correlationId;
  }
}

export function apiErrorFromStatus(
  status: number,
  correlationId: string,
): ApiError {
  let kind: ApiErrorKind;
  switch (status) {
    case 401:
      kind = "unauthenticated";
      break;
    case 403:
      kind = "forbidden";
      break;
    case 404:
      kind = "not_found";
      break;
    case 500:
    case 502:
    case 503:
    case 504:
      kind = "unavailable";
      break;
    default:
      kind = "unknown";
      break;
  }
  return new ApiError({ kind, status, correlationId });
}

export function apiErrorFromCause(
  cause: unknown,
  correlationId: string,
): ApiError {
  if (cause instanceof ApiError) {
    return cause;
  }
  return new ApiError({ kind: "unknown", status: null, correlationId });
}
