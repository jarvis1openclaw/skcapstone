# Fleet signer migration contract

Card `d0d6063a` replaces a shared agent signer with distinct scoped node and
service identities. This contract carries no private key, passphrase, secret
URL, or encrypted backup material.

Each identity has one accountable node or service, a secret-store reference,
exact purpose and audience limits, revocation state, and attributable audit
identity. Private key synchronization is forbidden. The Casey and Jarvis
identity keys remain trust anchors and cannot become application issuers.

The sequence is inventory, enrollment, shadow verification, human-approved
cutover, and legacy revocation. Failover selects only an explicitly approved
secondary identity. Rollback disables the new identity and selects the last
active issuer-policy revision. No stage automates enrollment, key creation,
terms acceptance, or production cutover.
