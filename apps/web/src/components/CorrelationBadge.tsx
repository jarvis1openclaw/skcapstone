/**
 * Correlation badge. Renders the correlation identifier for the current
 * view or failed request so it can be quoted to support and traced in
 * the audit log. Identifiers only, never protected record content.
 */

export function CorrelationBadge(props: {
  correlationId: string;
  label?: string;
}) {
  return (
    <span className="sl-correlation">
      <span className="sl-correlation-label">
        {props.label ?? "Correlation"}
      </span>
      <code className="sl-correlation-id">{props.correlationId}</code>
    </span>
  );
}
