import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI

from backend.v2.bootstrap import build_test_runtime


BOUNDARY = "vida-boundary"
JSON_MAX_BYTES = 16 * 1024


def multipart_body(*parts):
    body = bytearray()
    for name, value, filename, content_type in parts:
        body.extend(f"--{BOUNDARY}\r\n".encode())
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body.extend((disposition + "\r\n").encode())
        if content_type is not None:
            body.extend(f"Content-Type: {content_type}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(value if isinstance(value, bytes) else value.encode())
        body.extend(b"\r\n")
    body.extend(f"--{BOUNDARY}--\r\n".encode())
    return bytes(body)


class EpisodeSubmissionSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.runtime = build_test_runtime(self.data_dir, b"m" * 32)
        self.runtime.scheduler.notify = Mock()
        self.profile = self.runtime.require_provider_profiles().create_profile(
            "Primary",
            "https://api.example/v1",
            "secret-key",
            "model-a",
            0.1,
        )
        self.app = FastAPI()
        self.runtime.install(self.app)

    def tearDown(self):
        self.temp.cleanup()

    async def test_multipart_never_calls_request_form_or_creates_a_spool(self):
        body = self.valid_multipart(b"hello")
        with (
            patch("starlette.requests.Request.form", side_effect=AssertionError("form called")),
            patch(
                "starlette.formparsers.SpooledTemporaryFile",
                side_effect=AssertionError("spool created"),
            ),
        ):
            response, _ = await self.request(
                body,
                f"multipart/form-data; boundary={BOUNDARY}",
                chunk_size=7,
            )

        self.assertEqual(response["status"], 202, response)
        self.assertEqual(self.database_counts(), (1, 1))

    async def test_oversized_file_stops_consuming_promptly_and_cleans_partial(self):
        self.runtime.settings = self.runtime.settings.__class__(
            **{**self.runtime.settings.__dict__, "upload_max_bytes": 4}
        )
        tail = b"z" * 10000
        body = self.valid_multipart(b"12345") + tail

        response, consumed = await self.request(
            body,
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=64,
            include_content_length=False,
        )

        self.assertEqual(response["status"], 413, response)
        self.assertLess(consumed, len(body) // 10)
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(self.database_counts(), (0, 0))
        self.runtime.scheduler.notify.assert_not_called()

    async def test_multiple_file_parts_reject_before_unbounded_tail(self):
        prefix = multipart_body(
            ("providerProfileId", self.profile.id, None, None),
            ("summaryLanguage", "zh", None, None),
            ("file", b"one", "one.txt", "text/plain"),
            ("file", b"two", "two.txt", "text/plain"),
        )
        body = prefix + b"z" * 10000

        response, consumed = await self.request(
            body,
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=64,
            include_content_length=False,
        )

        self.assertEqual(response["status"], 400, response)
        self.assertEqual(response["json"]["error"]["code"], "invalid_source")
        self.assertLess(consumed, len(body) // 10)
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(self.database_counts(), (0, 0))

    async def test_oversized_text_field_rejects_before_unbounded_tail(self):
        prefix = multipart_body(
            ("providerProfileId", self.profile.id, None, None),
            ("summaryLanguage", "zh", None, None),
            ("title", "t" * 801, None, None),
            ("file", b"one", "one.txt", "text/plain"),
        )
        body = prefix + b"z" * 10000

        response, consumed = await self.request(
            body,
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=64,
            include_content_length=False,
        )

        self.assertEqual(response["status"], 422, response)
        self.assertLess(consumed, len(body) // 5)
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(self.database_counts(), (0, 0))

    async def test_oversized_boundary_is_rejected_before_parser_construction(self):
        content_type = "multipart/form-data; boundary=" + "x" * 71
        with patch(
            "backend.v2.services.episodes.MultipartParser",
            side_effect=AssertionError("unsafe parser construction"),
        ):
            response, consumed = await self.request(
                b"", content_type, chunk_size=1
            )

        self.assertEqual(response["status"], 400, response)
        self.assertEqual(response["json"]["error"]["code"], "invalid_source")
        self.assertEqual(consumed, 0)

    async def test_disconnect_closes_parser_resources_and_removes_partial(self):
        complete = self.valid_multipart(b"partial file body")
        cut = complete.find(b"partial file body") + len(b"partial")
        created_spools = []

        def spool_factory(*args, **kwargs):
            import tempfile as tempfile_module

            spool = tempfile_module.SpooledTemporaryFile(*args, **kwargs)
            created_spools.append(spool)
            return spool

        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=spool_factory):
            response, _ = await self.request(
                complete[:cut],
                f"multipart/form-data; boundary={BOUNDARY}",
                chunk_size=3,
                disconnect=True,
                include_content_length=False,
            )

        self.assertEqual(response["status"], 500, response)
        self.assertTrue(all(spool.closed for spool in created_spools))
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(self.database_counts(), (0, 0))

    async def test_exact_file_limit_is_accepted_and_max_plus_one_is_rejected(self):
        self.runtime.settings = self.runtime.settings.__class__(
            **{**self.runtime.settings.__dict__, "upload_max_bytes": 4}
        )
        exact, _ = await self.request(
            self.valid_multipart(b"1234"),
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=2,
        )
        over, _ = await self.request(
            self.valid_multipart(b"12345"),
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=2,
        )

        self.assertEqual(exact["status"], 202, exact)
        self.assertEqual(over["status"], 413, over)
        self.assertEqual(self.database_counts(), (1, 1))
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])

    async def test_json_never_calls_request_json_and_exact_body_limit_is_accepted(self):
        payload = self.valid_json()
        body = json.dumps(payload, separators=(",", ":")).encode()
        body += b" " * (JSON_MAX_BYTES - len(body))
        with patch(
            "starlette.requests.Request.json", side_effect=AssertionError("json called")
        ):
            response, consumed = await self.request(
                body, "application/json", chunk_size=19
            )

        self.assertEqual(len(body), JSON_MAX_BYTES)
        self.assertEqual(consumed, JSON_MAX_BYTES)
        self.assertEqual(response["status"], 202, response)

    async def test_json_max_plus_one_stops_at_limit_without_database_write(self):
        payload = json.dumps(self.valid_json(), separators=(",", ":")).encode()
        body = payload + b" " * (JSON_MAX_BYTES + 1 - len(payload)) + b"tail" * 1000

        response, consumed = await self.request(
            body,
            "application/json",
            chunk_size=257,
            include_content_length=False,
        )

        self.assertEqual(response["status"], 413, response)
        self.assertLessEqual(consumed, JSON_MAX_BYTES + 257)
        self.assertEqual(self.database_counts(), (0, 0))
        self.runtime.scheduler.notify.assert_not_called()

    async def test_json_content_length_rejects_without_consuming_body(self):
        body = b"x" * (JSON_MAX_BYTES + 1)
        response, consumed = await self.request(body, "application/json", chunk_size=257)

        self.assertEqual(response["status"], 413, response)
        self.assertEqual(consumed, 0)
        self.assertEqual(self.database_counts(), (0, 0))

    async def test_json_rejects_malformed_extra_and_overlong_fields(self):
        cases = (
            b"{not-json",
            json.dumps({**self.valid_json(), "ignored": True}).encode(),
            json.dumps({**self.valid_json(), "title": "t" * 201}).encode(),
            json.dumps({**self.valid_json(), "summaryLanguage": "z" * 33}).encode(),
            json.dumps({**self.valid_json(), "sourceUrl": "https://x.test/" + "a" * 2040}).encode(),
        )
        for body in cases:
            with self.subTest(size=len(body)):
                response, _ = await self.request(body, "application/json", chunk_size=11)
                self.assertEqual(response["status"], 422, response)
                self.assertEqual(response["json"]["error"]["code"], "validation_error")
        self.assertEqual(self.database_counts(), (0, 0))

    async def test_filename_utf8_component_boundary_and_overlong_cleanup(self):
        safe = "界" * 78 + "aa.txt"
        overlong = "界" * 79 + ".txt"
        exact, _ = await self.request(
            self.valid_multipart(b"ok", filename=safe),
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=13,
        )
        over, _ = await self.request(
            self.valid_multipart(b"no", filename=overlong),
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=13,
        )
        very_long, _ = await self.request(
            self.valid_multipart(b"no", filename="a" * 300 + ".txt"),
            f"multipart/form-data; boundary={BOUNDARY}",
            chunk_size=13,
        )

        self.assertEqual(exact["status"], 202, exact)
        self.assertEqual(over["status"], 400, over)
        self.assertEqual(over["json"]["error"]["code"], "invalid_source")
        self.assertEqual(very_long["status"], 400, very_long)
        self.assertEqual(very_long["json"]["error"]["code"], "invalid_source")
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(self.database_counts(), (1, 1))

    def valid_multipart(self, file_bytes, filename="clip.txt"):
        return multipart_body(
            ("providerProfileId", self.profile.id, None, None),
            ("summaryLanguage", "zh", None, None),
            ("file", file_bytes, filename, "text/plain"),
        )

    def valid_json(self):
        return {
            "sourceUrl": "https://example.com/video",
            "providerProfileId": self.profile.id,
            "summaryLanguage": "zh",
        }

    def database_counts(self):
        with self.runtime.database.connect() as conn:
            return (
                conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            )

    async def request(
        self,
        body,
        content_type,
        *,
        chunk_size,
        include_content_length=True,
        disconnect=False,
    ):
        chunks = [body[index : index + chunk_size] for index in range(0, len(body), chunk_size)]
        consumed = 0
        messages = []

        async def receive():
            nonlocal consumed
            if chunks:
                chunk = chunks.pop(0)
                consumed += len(chunk)
                return {"type": "http.request", "body": chunk, "more_body": True}
            if disconnect:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        headers = [(b"content-type", content_type.encode("latin-1"))]
        if include_content_length:
            headers.append((b"content-length", str(len(body)).encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v2/episodes",
            "raw_path": b"/api/v2/episodes",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        await self.app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return {
            "status": start["status"],
            "json": json.loads(response_body),
        }, consumed


if __name__ == "__main__":
    unittest.main()
