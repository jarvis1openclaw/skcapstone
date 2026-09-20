# Seraph approval publication for private SKGit

Card: `99bc5a11`. This source supports the exact SKLegal repository at
`https://skgit.skstack01.douno.it/smilinTux/sklegal`.

## Runtime state

Source tests and actual-card preflight pass. Live publication is not activated.
The CLI exposes local `preflight` only. It deliberately returns a blocked
publication result because it has no installed trusted authorization bindings.
An independent card PASS is not a forge approval.

Link consumes the mediated observation feed. Seraph owns independent review.
The approval connector uses a distinct forge service identity with access only
to the authorized repository. Link never receives that credential.

## Required bindings

The existing `publish_seraph_pass` function requires these trusted ports:

1. A `CapAuthVerifier` that authenticates `capauth:seraph@skworld.io`, verifies
   the exact `forge-review:publish` capability, and binds it to the publisher's
   request digest. A caller-supplied identity or boolean is insufficient.
2. A `ForgejoReviewConnector` with a credential attestor that verifies the live
   service identity, exact repository scope, and `write:repository` permission.
   A successful read or environment variable does not prove credential scope.
3. `LiveCardStoreGateway`, an approved evidence root, and a durable receipt
   directory owned by the publication service.

Existing signed-request and CapAuth authorization primitives can support an
adapter, but the current deployment has no qualified publisher binding, approved
capability rule/grant, or identified independent forge credential. This change
does not invent those authorities or create an account.

## Local evidence preflight

Build a JSON request using the fields of `SeraphPassEvidence`: `repository`,
`number`, `head_sha`, `source_card`, `source_card_revision`, `review_card`,
`review_card_revision`, `reviewer_identity`, `evidence_sha256`, and `verdict`
(exactly `"PASS"`). Use current
folded revisions and the independent review artifact hash. Do not put a token
or CapAuth presentation in that file.

```bash
python -m skcapstone.seraph_forgejo preflight \
  --request /path/to/review-request.json \
  --home "$HOME/.skcapstone" \
  --evidence-root "$HOME/.skcapstone/evidence/work"
```

Successful local validation reports `"local_evidence": "verified"` together with
the missing authorization bindings and exits nonzero. It performs no forge write.
Substantive card mutations change the folded revision, so rebuild the request
from current evidence immediately before authorized publication. The publisher's
own receipt links are excluded from that revision to keep retries stable.

## Publication checks

- Source and completed independent review agree on repository, PR, commit,
  candidate artifact hash, reviewer attribution, and current revisions.
- Review artifact bytes match both the card and request, without following
  symlinks. Unresolved sibling reviews block publication.
- The PR is open, its base equals protected main, and bounded immutable Git
  parent traversal proves main is an ancestor. More than 32 visited commits
  or an exhausted time budget fails closed.
- Required checks are currently green. Live protection requires approval,
  rejects outdated branches and rejected reviews, dismisses stale approvals,
  applies to administrators, and disables direct pushes.
- An unresolved rejection blocks publication. Readback requires an official,
  current, non-dismissed approval at the exact commit by the service identity.
- Retries reconcile the remote review with the durable receipt. A different
  review ID cannot satisfy an existing receipt. Receipt links use `skcapstone
  coord`, preserving the canonical verdict.

## Activation and rollback

After the source PR passes protected-main review, qualify the real authorization
and credential bindings on the intended host. First run private observations,
then publish one authorized approval and verify its exact remote review ID,
commit, service identity, and durable receipt. Only then enable automation.
Approval of an input repair does not approve a later combined candidate.

Rollback restores the previous executable and service configuration, disables
the new publication job, and preserves all remote review and receipt history.
No protection changes, application deployment, or corpus processing are needed.
