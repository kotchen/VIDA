import tempfile
import unittest
from pathlib import Path

from backend.v2.crypto import CredentialCipher
from backend.v2.database import Database
from backend.v2.repositories.provider_profiles import ProviderProfileRepository
from backend.v2.services.provider_profiles import ProviderProfileService


class ProviderProfileRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "db.sqlite3")
        self.db.initialize()
        self.service = ProviderProfileService(
            ProviderProfileRepository(self.db), CredentialCipher(b"m" * 32)
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
        credentials = self.service.get_revision_credentials(created.active_revision_id)
        self.assertEqual(credentials.model_id, "model-a")
