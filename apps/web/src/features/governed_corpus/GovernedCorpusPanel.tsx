import type { CorpusSpanRead, GovernedCorpusResult } from "./types";

export interface GovernedCorpusPanelProps {
  result: GovernedCorpusResult | null;
  span?: CorpusSpanRead | null;
  busy?: boolean;
  error?: string | null;
}

function EvidenceValue(props: { label: string; value: string | number }) {
  return (
    <div className="skl-corpus-evidence-row">
      <dt>{props.label}</dt>
      <dd className="skl-corpus-identifier" tabIndex={0}>
        {props.value}
      </dd>
    </div>
  );
}

export function GovernedCorpusPanel(props: GovernedCorpusPanelProps) {
  const { result, span = null, busy = false, error = null } = props;
  return (
    <section
      className="skl-governed-corpus"
      aria-labelledby="governed-corpus-heading"
      aria-busy={busy}
    >
      <header>
        <p className="skl-corpus-kicker">Governed Matter research</p>
        <h2 id="governed-corpus-heading">Corpus retrieval</h2>
        <p>
          Source-derived research proposals remain separate from Matter facts,
          verified Authority, model inference, and human decisions.
        </p>
      </header>
      {error ? <p role="alert">{error}</p> : null}
      {result === null ? (
        <p>No governed corpus query has run.</p>
      ) : (
        <>
          <dl className="skl-corpus-evidence" aria-label="Retrieval evidence">
            <EvidenceValue
              label="Release"
              value={result.projection.releaseId}
            />
            <EvidenceValue
              label="Projection generation"
              value={result.projection.projectionGeneration}
            />
            <EvidenceValue
              label="Watermark"
              value={`${result.projection.backendWatermark} of ${result.projection.coreWatermark}`}
            />
            <EvidenceValue
              label="Projection lag"
              value={`${result.projection.lagEvents} events / ${result.projection.lagSeconds} seconds`}
            />
            <EvidenceValue label="Query hash" value={result.querySha256} />
            <EvidenceValue
              label="Authorization decision"
              value={result.authorizationDecisionId}
            />
            <EvidenceValue
              label="Policy decision"
              value={result.policyDecisionId}
            />
            <EvidenceValue
              label="Rights revision"
              value={result.rightsRevision}
            />
            <EvidenceValue label="Rank mode" value={result.mode} />
          </dl>
          {result.noAnswer ? (
            <p role="status">
              No governed answer is available in the authorized Matter scope.
            </p>
          ) : (
            <ol aria-label="Governed corpus results">
              {result.hits.map((hit) => (
                <li key={hit.source.sourceVersionId}>
                  <article>
                    <h3>
                      <a href={`#corpus-span-${hit.source.sourceId}`}>
                        {hit.source.citation}
                      </a>
                    </h3>
                    <p>{hit.source.exactSpan}</p>
                    <p>
                      <strong>Research state:</strong>{" "}
                      {hit.source.verificationState.replaceAll("_", " ")}
                    </p>
                    <dl className="skl-corpus-evidence">
                      <EvidenceValue
                        label="Rank path"
                        value={hit.rank.rankPath.join(" then ")}
                      />
                      <EvidenceValue label="Rank" value={hit.rank.rank} />
                      <EvidenceValue
                        label="Source hash"
                        value={hit.source.sourceSha256}
                      />
                      <EvidenceValue
                        label="Chunk hash"
                        value={hit.source.chunkSha256}
                      />
                      <EvidenceValue
                        label="Exact locator"
                        value={`${hit.source.locator.kind}:${hit.source.locator.start}:${hit.source.locator.end}`}
                      />
                      <EvidenceValue
                        label="Supersession"
                        value={hit.supersessionStatus}
                      />
                    </dl>
                  </article>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
      {span ? (
        <article
          id={`corpus-span-${span.source.sourceId}`}
          aria-label="Exact source span"
          tabIndex={-1}
        >
          <h3>Exact source span</h3>
          <blockquote>{span.source.exactSpan}</blockquote>
          <EvidenceValue label="Citation" value={span.source.citation} />
          <EvidenceValue label="Source hash" value={span.source.sourceSha256} />
          <EvidenceValue
            label="Rights revision"
            value={span.source.rightsRevision}
          />
        </article>
      ) : null}
      <footer>
        <p>
          Qdrant and FalkorDB: metadata compatibility only. No provider request
          or external action occurs from this surface.
        </p>
      </footer>
    </section>
  );
}
