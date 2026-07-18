import base64
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.v2.crypto import CredentialCipher, EncryptedCredential
from backend.v2.database import Database
from backend.v2.repositories.provider_profiles import ProviderProfileRepository
from backend.v2.services.provider_profiles import ProviderProfileService


class CredentialCipherTests(unittest.TestCase):
    def setUp(self):
        self.cipher = CredentialCipher(b"m" * 32)

    def assert_decryption_fails_closed(
        self, cipher: CredentialCipher, envelope: EncryptedCredential, plaintext: str
    ):
        with self.assertRaises(Exception) as caught:
            cipher.decrypt(envelope)
        self.assertNotIn(plaintext, str(caught.exception))

    def test_same_plaintext_uses_independent_authenticated_envelopes(self):
        first = self.cipher.encrypt("same-secret")
        second = self.cipher.encrypt("same-secret")

        self.assertNotEqual(first.nonce, second.nonce)
        self.assertNotEqual(first.ciphertext, second.ciphertext)
        self.assertEqual(self.cipher.decrypt(first), "same-secret")
        self.assertEqual(self.cipher.decrypt(second), "same-secret")

    def test_invalid_envelopes_and_wrong_key_fail_closed(self):
        plaintext = "secret-that-must-not-leak"
        envelope = self.cipher.encrypt(plaintext)
        tampered = bytearray(base64.b64decode(envelope.ciphertext))
        tampered[0] ^= 1

        cases = (
            (self.cipher, replace(envelope, format_version=2)),
            (
                self.cipher,
                replace(envelope, ciphertext=base64.b64encode(tampered).decode("ascii")),
            ),
            (self.cipher, replace(envelope, ciphertext="%%%not-base64%%%")),
            (self.cipher, replace(envelope, nonce="%%%not-base64%%%")),
            (CredentialCipher(b"w" * 32), envelope),
        )
        for cipher, invalid_envelope in cases:
            with self.subTest(envelope=invalid_envelope):
                self.assert_decryption_fails_closed(cipher, invalid_envelope, plaintext)


class ProviderProfileRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "db.sqlite3")
        self.db.initialize()
        self.repository = ProviderProfileRepository(self.db)
        self.service = ProviderProfileService(
            self.repository, CredentialCipher(b"m" * 32)
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_key_is_encrypted_and_update_creates_revision(self):
        created = self.service.create_profile(
            "Primary", "https://api.example/v1", "secret-key", "model-a", 0.1
        )
        updated = self.service.update_profile(created.id, model_id="model-b")
        self.assertEqual(created.revision, 1)
        self.assertEqual(updated.revision, 2)
        first = self.service.get_revision_credentials(created.active_revision_id)
        second = self.service.get_revision_credentials(updated.active_revision_id)
        self.assertEqual(first.api_key, "secret-key")
        self.assertEqual(second.api_key, "secret-key")
        self.assertEqual(first.model_id, "model-a")
        self.assertEqual(second.model_id, "model-b")
        self.assertFalse(hasattr(created, "api_key"))
        self.assertEqual(created.api_key_masked, "••••••••-key")
        with self.db.connect() as conn:
            stored = conn.execute(
                "SELECT encrypted_api_key FROM provider_profile_revisions WHERE id=?",
                (created.active_revision_id,),
            ).fetchone()[0]
        self.assertNotIn("secret-key", stored)

    def test_delete_hides_profile_but_keeps_revision(self):
        created = self.service.create_profile(
            "Primary", "https://api.example/v1", "secret-key", "model-a", 0.1
        )
        self.service.delete_profile(created.id)
        self.assertEqual(self.service.list_profiles(), [])
        self.assertIsNone(self.service.get_profile(created.id))
        with self.assertRaises(KeyError):
            self.service.update_profile(created.id, model_id="model-b")
        credentials = self.service.get_revision_credentials(created.active_revision_id)
        self.assertEqual(credentials.model_id, "model-a")

    def test_short_keys_are_fully_masked_and_long_key_exposes_only_last_four(self):
        for api_key in ("a", "ab", "abc", "abcd"):
            with self.subTest(api_key=api_key):
                profile = self.service.create_profile(
                    api_key, "https://api.example/v1", api_key, "model-a", 0.1
                )
                self.assertNotIn(api_key, profile.api_key_masked)

        longer = self.service.create_profile(
            "Long", "https://api.example/v1", "abcdef", "model-a", 0.1
        )
        self.assertEqual(longer.api_key_masked, "\u2022" * 8 + "cdef")

    def test_new_key_creates_revision_without_changing_old_credentials(self):
        created = self.service.create_profile(
            "Primary", "https://api.example/v1", "old-secret", "model-a", 0.1
        )
        updated = self.service.update_profile(created.id, api_key="new-secret")

        self.assertEqual(
            self.service.get_revision_credentials(created.active_revision_id).api_key,
            "old-secret",
        )
        self.assertEqual(
            self.service.get_revision_credentials(updated.active_revision_id).api_key,
            "new-secret",
        )

    def test_omitted_key_preserves_complete_encrypted_envelope(self):
        created = self.service.create_profile(
            "Primary", "https://api.example/v1", "secret-key", "model-a", 0.1
        )
        with self.db.connect() as conn:
            before = tuple(
                conn.execute(
                    """SELECT encrypted_api_key, encryption_nonce,
                              encryption_format_version
                       FROM provider_profile_revisions WHERE id=?""",
                    (created.active_revision_id,),
                ).fetchone()
            )

        updated = self.service.update_profile(created.id, model_id="model-b")
        with self.db.connect() as conn:
            after = tuple(
                conn.execute(
                    """SELECT encrypted_api_key, encryption_nonce,
                              encryption_format_version
                       FROM provider_profile_revisions WHERE id=?""",
                    (updated.active_revision_id,),
                ).fetchone()
            )
        self.assertEqual(after, before)

    def test_repeated_updates_create_monotonic_immutable_revisions(self):
        profiles = [
            self.service.create_profile(
                "Primary", "https://api.example/v1", "secret-key", "model-1", 0.1
            )
        ]
        for version in (2, 3, 4):
            profiles.append(
                self.service.update_profile(profiles[0].id, model_id=f"model-{version}")
            )

        self.assertEqual([profile.revision for profile in profiles], [1, 2, 3, 4])
        credentials = [
            self.service.get_revision_credentials(profile.active_revision_id)
            for profile in profiles
        ]
        self.assertEqual(
            [item.model_id for item in credentials],
            ["model-1", "model-2", "model-3", "model-4"],
        )

    def test_failed_create_rolls_back_profile_and_revision(self):
        with patch(
            "backend.v2.repositories.provider_profiles._insert_revision",
            side_effect=RuntimeError("injected revision insert failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected revision insert failure"):
                self.service.create_profile(
                    "Primary", "https://api.example/v1", "secret-key", "model-a", 0.1
                )

        with self.db.connect() as conn:
            profile_count = conn.execute("SELECT COUNT(*) FROM provider_profiles").fetchone()[0]
            revision_count = conn.execute(
                "SELECT COUNT(*) FROM provider_profile_revisions"
            ).fetchone()[0]
        self.assertEqual(profile_count, 0)
        self.assertEqual(revision_count, 0)
