from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sklegal_capauth import (
    CustodyValidationError,
    parse_gpg_capabilities,
    read_owner_passphrase,
    validate_declared_capabilities,
    validate_key_coherence,
)


def gpg_record(kind: str, key_id: str, capabilities: str) -> str:
    fields = [kind, "u", "255", "22", key_id, "0", "0", "", "", "", "", capabilities]
    return ":".join(fields)


class CustodyValidationTests(unittest.TestCase):
    def write_passphrase(self, value: bytes, mode: int = 0o600) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False)
        path = Path(handle.name)
        try:
            handle.write(value)
        finally:
            handle.close()
        os.chmod(path, mode)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_passphrase_loader_normalizes_one_terminal_newline_only(self) -> None:
        plain = self.write_passphrase(b"secret")
        lf = self.write_passphrase(b"secret\n")
        crlf = self.write_passphrase(b"secret\r\n")
        self.assertEqual(b"secret", read_owner_passphrase(plain))
        self.assertEqual(read_owner_passphrase(plain), read_owner_passphrase(lf))
        self.assertEqual(read_owner_passphrase(plain), read_owner_passphrase(crlf))

    def test_passphrase_loader_rejects_empty_nul_multiline_and_unsafe_files(
        self,
    ) -> None:
        for value in (b"", b"secret\nagain", b"secret\x00tail"):
            with self.subTest(value=value):
                with self.assertRaises(CustodyValidationError):
                    read_owner_passphrase(self.write_passphrase(value))
        with self.assertRaises(CustodyValidationError):
            read_owner_passphrase(self.write_passphrase(b"secret", mode=0o644))

    def test_capability_doctor_rejects_encryption_only_declared_signing_subkey(
        self,
    ) -> None:
        lines = (
            gpg_record("pub", "PRIMARY", "scESC"),
            "fpr:::::::::PRIMARY-FINGERPRINT:",
            gpg_record("sub", "ENCRYPTION", "e"),
            "fpr:::::::::ENCRYPTION-FINGERPRINT:",
        )
        records = parse_gpg_capabilities(lines)
        with self.assertRaises(CustodyValidationError):
            validate_declared_capabilities(records, ("ENCRYPTION-FINGERPRINT",))

    def test_capability_doctor_accepts_signing_subkey_and_coherence_probe(self) -> None:
        lines = (
            gpg_record("sec", "PRIMARY", "sc"),
            "fpr:::::::::PRIMARY-FINGERPRINT:",
            gpg_record("ssb", "SIGNING", "s"),
            "fpr:::::::::SIGNING-FINGERPRINT:",
        )
        records = validate_declared_capabilities(
            parse_gpg_capabilities(lines), ("SIGNING-FINGERPRINT",)
        )
        self.assertEqual(2, len(records))
        result = validate_key_coherence(
            private_fingerprint="PRIMARY-FINGERPRINT",
            public_fingerprint="PRIMARY-FINGERPRINT",
            passphrase=b"secret",
            unlock_probe=lambda value: value == b"secret",
        )
        self.assertTrue(result.passphrase_accepted)


if __name__ == "__main__":
    unittest.main()
