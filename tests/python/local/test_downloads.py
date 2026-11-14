"""Атомарная загрузка: старый файл сохраняется, временные файлы удаляются."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import Mock, patch
from local_mode.downloads import download_file


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name) / "model.bin"

    def response(self, chunks):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = chunks
        return response

    def test_verified_existing_file_skips_network(self):
        self.target.write_bytes(b"good")
        with patch("local_mode.downloads.requests.get") as get:
            download_file("https://example.com/model", self.target, max_bytes=10, sha256=hashlib.sha256(b"good").hexdigest())
            get.assert_not_called()

    def test_hash_failure_preserves_old_file_and_removes_partial(self):
        self.target.write_bytes(b"old")
        with patch("local_mode.downloads.requests.get", return_value=self.response([b"wrong"])):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                download_file("https://example.com/model", self.target, max_bytes=10, sha256=hashlib.sha256(b"good").hexdigest())
        self.assertEqual(self.target.read_bytes(), b"old")
        self.assertEqual(list(self.target.parent.glob("*.part")), [])

    def test_network_failure_and_size_limit_leave_no_partial(self):
        def broken():
            yield b"first"
            raise OSError("interrupted")
        for chunks, maximum in [(broken(), 20), ([b"too large"], 3)]:
            with patch("local_mode.downloads.requests.get", return_value=self.response(chunks)):
                with self.assertRaises((OSError, ValueError)):
                    download_file("https://example.com/model", self.target, max_bytes=maximum)
            self.assertFalse(self.target.exists())
            self.assertEqual(list(self.target.parent.glob("*.part")), [])

    def test_concurrent_downloads_do_not_share_partial_file(self):
        barrier = Barrier(2)
        def chunks():
            yield b"same-model"
            barrier.wait(timeout=5)
        def response(*args, **kwargs):
            return self.response(chunks())
        def run(_):
            download_file("https://example.com/model", self.target, max_bytes=20, sha256=hashlib.sha256(b"same-model").hexdigest())
        with patch("local_mode.downloads.requests.get", side_effect=response), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, range(2)))
        self.assertEqual(self.target.read_bytes(), b"same-model")
        self.assertEqual(list(self.target.parent.glob("*.part")), [])
