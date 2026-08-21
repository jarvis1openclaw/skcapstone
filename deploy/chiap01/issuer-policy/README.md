# SKLegal trusted issuer policy

`trusted-issuers.json` is the versioned public authority ceiling for the
dedicated SKLegal application issuer. `trusted-issuers.json.asc` is Casey's
detached governance signature over the exact JSON bytes.

Current public anchors:

- Casey primary and signing fingerprint:
  `AD80D077A047BABF29EEC97AF454FDBC3B1C37D9`
- Casey encryption-only subkey:
  `6307CFC60607A7FBADA1CF6425223774EF4D20D1`
- Manifest SHA-256:
  `cb7161dba028ece024bafc77eea39f1b580ceb256239909ef7a0f06a5829afcd`
- Detached signature SHA-256:
  `a1db46980f479a4780ddfa2462defc15174255e2f934fb0b7540dfa6d1696d20`

## Verify

Import Casey's public key into an isolated verification keyring and run:

```bash
/usr/bin/gpg --homedir "$verification_home" --batch \
  --status-fd 1 \
  --verify trusted-issuers.json.asc trusted-issuers.json
```

Accept only `VALIDSIG` for the exact Casey primary fingerprint above. The
signature is invalid as soon as any JSON byte changes.

## Sign on chiap08

The authoritative private key and owner-only credential stay on `chiap08`.
Use `/usr/bin/gpg`, an isolated tmpfs keyring, the exact Casey primary
fingerprint, and file-descriptor credential input. Never put the credential in
argv, environment variables, logs, receipts, or transferred files.

The owner-only credential file is a normal text file with one terminal line
ending. GnuPG's `--passphrase-fd` consumes the first line correctly. A direct
Python or PGPy adapter must remove exactly one terminal `LF` or `CRLF` before
unlocking. It must reject empty, multiline, NUL-containing, linked,
wrong-owner, or unsafe-mode files. Do not use broad whitespace stripping.

The current Casey key does not need to be re-exported. Its private and public
armor both resolve to the primary fingerprint above. The named subkey is
encryption-only and cannot be selected for signing.

After signing, independently verify the detached signature with public
material, transfer only the JSON and ASCII-armored signature, and remove the
exact isolated signing and verification scratch directories.
