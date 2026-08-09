import tempfile
import unittest
import zipfile
from pathlib import Path

from mangamillion_downloader import build_epub


class EpubBuildTests(unittest.TestCase):
    def test_missing_title_directory_error_lists_available_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Hunter x Hunter").mkdir()

            with self.assertRaises(FileNotFoundError) as err:
                build_epub(root / "Title Name", language="en")

            message = str(err.exception)
            self.assertIn("title directory does not exist", message)
            self.assertIn("Available title directories:", message)
            self.assertIn("Hunter x Hunter", message)

    def test_build_epub_packages_downloaded_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            title_dir = root / "Sample Manga"
            chapter_one = title_dir / "1 First Chapter"
            chapter_two = title_dir / "2 Second Chapter"
            chapter_one.mkdir(parents=True)
            chapter_two.mkdir()
            (chapter_one / "001.webp").write_bytes(b"RIFF....WEBPpage-one")
            (chapter_one / "002.webp").write_bytes(b"RIFF....WEBPpage-two")
            (chapter_two / "001.webp").write_bytes(b"RIFF....WEBPpage-three")

            epub_path = build_epub(
                title_dir,
                root / "Sample Manga.epub",
                title="Sample Manga",
                author="A. Writer",
                language="en",
            )

            self.assertEqual(epub_path, root / "Sample Manga.epub")
            with zipfile.ZipFile(epub_path) as archive:
                names = archive.namelist()
                self.assertEqual(names[0], "mimetype")
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                self.assertIn("META-INF/container.xml", names)
                self.assertIn("EPUB/package.opf", names)
                self.assertIn("EPUB/nav.xhtml", names)
                self.assertIn("EPUB/chapters/chapter_001.xhtml", names)
                self.assertIn("EPUB/chapters/chapter_002.xhtml", names)
                self.assertIn("EPUB/images/chapter_001/001.webp", names)
                self.assertIn("EPUB/images/chapter_001/002.webp", names)
                self.assertIn("EPUB/images/chapter_002/001.webp", names)
                chapter = archive.read("EPUB/chapters/chapter_001.xhtml").decode("utf-8")
                self.assertIn("../images/chapter_001/001.webp", chapter)
                self.assertIn("../images/chapter_001/002.webp", chapter)


if __name__ == "__main__":
    unittest.main()
