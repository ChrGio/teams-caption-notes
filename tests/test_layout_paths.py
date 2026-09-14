import unittest
from pathlib import Path
from unittest.mock import patch

import notes_window


class LayoutPathTests(unittest.TestCase):
    def test_source_setup_guide_uses_repository_docs_not_runtime_data_folder(self):
        with patch.object(notes_window.sys, "frozen", False, create=True):
            guide = notes_window.setup_guide_path(Path("some-source-data-folder"))
        self.assertEqual(guide, Path(notes_window.__file__).resolve().parents[1] / "docs" / "CHAT_NOTES_SETUP.md")
        self.assertTrue(guide.is_file())

    def test_packaged_setup_guide_keeps_original_location(self):
        base = Path("portable-app")
        with patch.object(notes_window.sys, "frozen", True, create=True):
            self.assertEqual(notes_window.setup_guide_path(base), base / "CHAT_NOTES_SETUP.md")


if __name__ == "__main__":
    unittest.main()

