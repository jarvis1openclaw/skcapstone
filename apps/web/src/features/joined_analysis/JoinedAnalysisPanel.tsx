import { MatterAnalysisRead } from "./contract";

export type JoinedAnalysisPanelState =
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | { kind: "ready"; analysis: MatterAnalysisRead };

export function JoinedAnalysisPanel({
  state,
}: {
  state: JoinedAnalysisPanelState;
}) {
  if (state.kind === "loading") {
    return (
      <section aria-label="Joined Matter analysis" aria-busy="true">
        Loading joined analysis.
      </section>
    );
  }
  if (state.kind === "unavailable") {
    return (
      <section aria-label="Joined Matter analysis unavailable" role="status">
        <h2>Joined analysis unavailable</h2>
        <p>{state.message}</p>
        <button type="button" disabled aria-disabled="true">
          No action available
        </button>
      </section>
    );
  }
  const { analysis } = state;
  return (
    <section aria-label="Joined Matter analysis">
      <header>
        <h2>Issues, Claims, Defenses, and Authority</h2>
        <p>{analysis.forum?.name ?? "Forum not recorded"}</p>
        <p>
          Snapshot <code>{analysis.snapshot.projectionSha256}</code>,{" "}
          {analysis.classification.value},{" "}
          {analysis.classification.sourceRights}
        </p>
      </header>
      {analysis.theories.length === 0 ? (
        <p>The authorized snapshot contains no Claims or Defenses.</p>
      ) : (
        analysis.theories.map((theory) => (
          <article
            key={theory.theoryId}
            aria-label={`${theory.theoryKind} ${theory.label}`}
          >
            <h3>{theory.label}</h3>
            <p>{theory.statement}</p>
            <dl>
              <dt>Status</dt>
              <dd>{theory.status}</dd>
              <dt>Support</dt>
              <dd>
                {
                  theory.evidence.filter((item) => item.role === "support")
                    .length
                }
              </dd>
              <dt>Counter-support</dt>
              <dd>
                {
                  theory.evidence.filter(
                    (item) => item.role === "counter_support",
                  ).length
                }
              </dd>
              <dt>Contrary Authority</dt>
              <dd>
                {
                  theory.authorities.filter(
                    (item) => item.role === "contrary_authority",
                  ).length
                }
              </dd>
              <dt>Blocking gaps</dt>
              <dd>{theory.gaps.filter((item) => item.blocking).length}</dd>
            </dl>
          </article>
        ))
      )}
    </section>
  );
}
