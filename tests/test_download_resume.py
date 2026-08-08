import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mangamillion_downloader as downloader


class FakeClient:
    throttle = 0

    def __init__(self):
        self.image_requests = 0

    def api(self, path, params=None, method="GET"):
        return b"unused"

    def _img(self, url):
        self.image_requests += 1
        return 200, b"x" * 64


class DownloadResumeTests(unittest.TestCase):
    def test_chapter_is_complete_only_when_all_expected_pages_are_larger_than_one_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            chapter_dir = Path(tmp)
            (chapter_dir / "001.webp").write_bytes(b"ok")
            (chapter_dir / "002.webp").write_bytes(b"x")

            self.assertFalse(downloader.chapter_already_downloaded(chapter_dir, 2))

            (chapter_dir / "002.webp").write_bytes(b"ok")

            self.assertTrue(downloader.chapter_already_downloaded(chapter_dir, 2))

    def test_download_title_redownloads_one_byte_webp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            title_dir = Path(tmp) / "Sample Manga" / "1 First"
            title_dir.mkdir(parents=True)
            page = title_dir / "001.webp"
            page.write_bytes(b"x")
            client = FakeClient()

            with (
                patch.object(downloader, "parse_title_detail", return_value={
                    "name": "Sample Manga",
                    "author": "A. Writer",
                    "description": "",
                    "coverUrl": "",
                }),
                patch.object(downloader, "parse_chapter_list", return_value=[{
                    "number": "1",
                    "name": "First",
                    "id": 101,
                }]),
                patch.object(downloader, "parse_viewer", return_value={
                    "pages": ["https://example.test/001.webp"],
                    "aesKey": "0" * 64,
                    "aesIv": "0" * 32,
                }),
                patch.object(downloader, "aes_decrypt", return_value=b"RIFFvalid-webp"),
                patch.object(downloader.time, "sleep"),
            ):
                downloader.download_title(client, 1, tmp, "en")

            self.assertEqual(client.image_requests, 1)
            self.assertGreater(page.stat().st_size, 1)


if __name__ == "__main__":
    unittest.main()
