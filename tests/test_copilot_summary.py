import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import copilot_summary as copilot


class CopilotConfigTests(unittest.TestCase):
    def test_default_configuration_is_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "copilot-config.json"
            copilot.ensure_config(path)
            config = copilot.load_config(path)
        self.assertFalse(config.enabled)
        self.assertFalse(config.configured)

    def test_loads_configured_public_client(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "copilot-config.json"
            path.write_text(json.dumps({
                "enabled": True,
                "tenant_id": "tenant-guid",
                "client_id": "client-guid",
            }), encoding="utf-8")
            config = copilot.load_config(path)
        self.assertTrue(config.enabled)
        self.assertTrue(config.configured)


class CopilotApiTests(unittest.TestCase):
    def test_extracts_last_copilot_message(self):
        config = copilot.CopilotConfig(True, True, "tenant", "client", "America/New_York", "Summarize", 10000)
        create_response = Mock(ok=True, status_code=201)
        create_response.json.return_value = {"id": "conversation-1"}
        chat_response = Mock(ok=True, status_code=200)
        chat_response.json.return_value = {
            "messages": [
                {"text": "Summarize"},
                {"text": "## Executive Summary\nDone.", "attributions": []},
            ]
        }
        with patch("copilot_summary.get_access_token", return_value="token"), patch(
            "copilot_summary.requests.post", side_effect=[create_response, chat_response]
        ) as post:
            summary, attributions = copilot.ask_copilot(config, "meeting transcript")
        self.assertEqual(summary, "## Executive Summary\nDone.")
        self.assertEqual(attributions, [])
        self.assertFalse(post.call_args_list[1].kwargs["json"]["contextualResources"]["webContext"]["isWebEnabled"])

    def test_summary_file_is_written_beside_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transcript = root / "Standup.md"
            transcript.write_text("Transcript", encoding="utf-8")
            config_path = root / "copilot-config.json"
            config_path.write_text(json.dumps({"tenant_id": "tenant", "client_id": "client"}), encoding="utf-8")
            with patch("copilot_summary.ask_copilot", return_value=("Summary text", [])):
                result = copilot.summarize_transcript(transcript, "Standup", config_path)
            content = result.read_text(encoding="utf-8")
        self.assertEqual(result.name, "Standup-summary.md")
        self.assertIn("Summary text", content)


if __name__ == "__main__":
    unittest.main()
