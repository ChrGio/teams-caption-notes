import hashlib
import json
import os
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import app_update as update


def executable(suffix=b"new"):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\x00\x00"
    struct.pack_into("<H", data, 68, 0x8664)
    struct.pack_into("<H", data, 88, 0x20B)
    return bytes(data) + suffix


def release(data=None, version="4.5"):
    data = data if data is not None else executable()
    return update.Release(version, "v" + version,
                          f"https://github.com/{update.REPOSITORY}/releases/download/v{version}/TeamsCaptionNotes-v{version}.exe",
                          hashlib.sha256(data).hexdigest(), len(data),
                          f"https://github.com/{update.REPOSITORY}/releases/tag/v{version}")


def metadata(item=None):
    item = item or release()
    return {"draft": False, "prerelease": False, "tag_name": item.tag,
            "html_url": item.html_url,
            "assets": [{"name": item.url.rsplit("/", 1)[-1], "size": item.size,
                        "digest": "sha256:" + item.sha256, "browser_download_url": item.url}]}


def response(data=b"", status=200, headers=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.status_code = status
    result.headers = headers or {}
    result.iter_content.return_value = [data]
    return result


class ReleaseTests(unittest.TestCase):
    def check(self, info, current="4.4"):
        result = response(json.dumps(info).encode())
        with patch.object(update, "_session") as session:
            session.return_value.__enter__.return_value.get.return_value = result
            return update.check_for_update(current)

    def test_new_stable_release_uses_fixed_public_endpoint(self):
        with patch.object(update, "_session") as session:
            get = session.return_value.__enter__.return_value.get
            get.return_value = response(json.dumps(metadata()).encode())
            self.assertEqual(update.check_for_update("4.4"), release())
            self.assertEqual(get.call_args.args, (update.LATEST_URL,))
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            self.assertEqual(get.call_args.kwargs["timeout"], update.NETWORK_TIMEOUT)

    def test_equal_older_and_patch_comparison(self):
        self.assertIsNone(self.check(metadata(), "4.5.0"))
        self.assertIsNone(self.check(metadata(), "4.10"))
        self.assertEqual(self.check(metadata(release(version="4.10"))).version, "4.10")

    def test_patch_release_is_available_from_minor_version(self):
        patch_release = release(version="4.5.1")
        self.assertEqual(self.check(metadata(patch_release), "4.5"), patch_release)

    def test_minor_release_follows_patch_without_reoffering_same_patch(self):
        self.assertEqual(self.check(metadata(release(version="4.6")), "4.5.1").version, "4.6")
        self.assertIsNone(self.check(metadata(release(version="4.5.1")), "4.5.1"))

    def test_4_5_2_updates_previous_patch_but_is_not_reoffered_or_downgraded(self):
        patch_release = release(version="4.5.2")
        self.assertEqual(self.check(metadata(patch_release), "4.5.1"), patch_release)
        self.assertIsNone(self.check(metadata(patch_release), "4.5.2"))
        self.assertIsNone(self.check(metadata(release(version="4.5.1")), "4.5.2"))

    def test_draft_and_prerelease_are_ignored(self):
        for key in ("draft", "prerelease"):
            item = metadata()
            item[key] = True
            self.assertIsNone(self.check(item))

    def test_4_5_3_updates_previous_patch_but_is_not_reoffered_or_downgraded(self):
        patch_release = release(version="4.5.3")
        self.assertEqual(self.check(metadata(patch_release), "4.5.2"), patch_release)
        self.assertIsNone(self.check(metadata(patch_release), "4.5.3"))
        self.assertIsNone(self.check(metadata(release(version="4.5.2")), "4.5.3"))

    def test_draft_and_prerelease_are_ignored_for_current_patch(self):
        for key in ("draft", "prerelease"):
            item = metadata(release(version="4.5.3"))
            item[key] = True
            self.assertIsNone(self.check(item))

    def test_invalid_tags_are_rejected(self):
        for tag in ("v4.5-beta", "v4.5;calc", "4.5", "v4.5/../../x", None):
            item = metadata()
            item["tag_name"] = tag
            with self.subTest(tag=tag), self.assertRaises(update.UpdateError):
                self.check(item)

    def test_missing_digest_is_fail_closed(self):
        item = metadata()
        del item["assets"][0]["digest"]
        with self.assertRaisesRegex(update.UpdateError, "SHA-256"):
            self.check(item)

    def test_multiple_executables_are_ambiguous(self):
        item = metadata()
        item["assets"].append(item["assets"][0].copy())
        with self.assertRaisesRegex(update.UpdateError, "exactly one"):
            self.check(item)

    def test_asset_version_and_filename_must_match(self):
        for name in ("TeamsCaptionNotes-v4.4.exe", "TeamsCaptionNotes-v4.5.exe"):
            item = metadata()
            item["assets"][0]["name"] = name
            item["assets"][0]["browser_download_url"] = item["assets"][0]["browser_download_url"].replace(
                "TeamsCaptionNotes-v4.5.exe", "TeamsCaptionNotes-v4.4.exe")
            with self.subTest(name=name), self.assertRaises(update.UpdateError):
                self.check(item)

    def test_fixed_repository_https_and_no_credentials(self):
        for url in ("http://github.com/ChrGio/teams-caption-notes/releases/download/v4.5/TeamsCaptionNotes-v4.5.exe",
                    release().url.replace("ChrGio", "someone"),
                    release().url.replace("github.com", "github.com.evil.test"),
                    release().url.replace("github.com", "user:password@github.com"),
                    release().url + "?untrusted=1"):
            with self.subTest(url=url), self.assertRaises(update.UpdateError):
                update._validate_release(replace(release(), url=url))

    def test_no_release_404_is_not_an_error(self):
        with patch.object(update, "_session") as session:
            session.return_value.__enter__.return_value.get.return_value = response(status=404)
            self.assertIsNone(update.check_for_update("4.4"))

    def test_api_redirect_and_rate_limit_are_not_followed(self):
        for status in (302, 403, 429, 500):
            with self.subTest(status=status), patch.object(update, "_session") as session:
                session.return_value.__enter__.return_value.get.return_value = response(status=status)
                with self.assertRaises(update.UpdateError):
                    update.check_for_update("4.4")

    def test_implicit_authorization_is_removed(self):
        request = MagicMock()
        request.headers = {"Authorization": "secret", "Other": "ok"}
        update._NoAuth()(request)
        self.assertEqual(request.headers, {"Other": "ok"})


class LocalUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "TeamsCaptionNotes-v4.4.exe"
        self.old = executable(b"old")
        self.target.write_bytes(self.old)
        self.data = executable()
        self.item = release(self.data)

    def download(self, responses=None):
        with patch.object(update, "_session") as session:
            get = session.return_value.__enter__.return_value.get
            get.side_effect = responses or [response(self.data)]
            candidate = update.download_update(self.item, self.target)
            return candidate, get.call_args_list

    def prepare(self):
        candidate, _ = self.download()
        with patch.object(update.sys, "frozen", True, create=True), patch.object(
                update.sys, "executable", str(self.target)), patch.object(
                update, "_process_created", return_value=123):
            manifest = update.prepare_install(candidate, self.target, self.item)
        # The helper is a separate process; use a safe fake old PID for mocked waits.
        plan = json.loads(manifest.read_text())
        plan["old_pid"] = 98765
        manifest.write_text(json.dumps(plan))
        return manifest

    def apply(self, manifest, **kwargs):
        with patch.object(update.sys, "frozen", True, create=True), patch.object(
                update.sys, "executable", str(manifest.with_name("updater.exe"))), patch.object(
                update, "_wait_for_old_process") as wait, patch.object(
                update, "_await_authorization"), patch.object(update, "_restart", **kwargs) as restart:
            result = update.apply_update(manifest)
        return result, wait, restart

    def test_download_verifies_and_stages_without_execution(self):
        with patch.object(update.subprocess, "Popen") as spawn:
            candidate, calls = self.download()
        self.assertEqual(candidate.read_bytes(), self.data)
        self.assertEqual(candidate.parent.parent, self.root / ".updates")
        self.assertEqual(self.target.read_bytes(), self.old)
        spawn.assert_not_called()
        self.assertFalse(calls[0].kwargs["allow_redirects"])

    def test_supported_github_asset_redirect_is_followed_manually(self):
        candidate, calls = self.download([response(status=302, headers={
            "Location": "https://release-assets.githubusercontent.com/github-production-release-asset/test?sig=example"}),
            response(self.data)])
        self.assertTrue(candidate.exists())
        self.assertEqual(len(calls), 2)

    def test_untrusted_or_http_redirect_is_never_requested(self):
        for url in ("https://evil.test/payload", "http://release-assets.githubusercontent.com/payload"):
            with self.subTest(url=url), patch.object(update, "_session") as session:
                get = session.return_value.__enter__.return_value.get
                get.return_value = response(status=302, headers={"Location": url})
                with self.assertRaises(update.UpdateError):
                    update.download_update(self.item, self.target)
                self.assertEqual(get.call_count, 1)

    def test_checksum_truncation_and_oversize_are_rejected(self):
        for data in (self.data[:-1], self.data + b"too much", executable(b"bad")):
            with self.subTest(data=data[-8:]), self.assertRaises(update.UpdateError):
                self.download([response(data)])
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_html_and_wrong_architecture_cannot_be_installed(self):
        wrong_arch = bytearray(self.data)
        struct.pack_into("<H", wrong_arch, 68, 0x14C)
        for data in (b"<html>not an exe</html>", bytes(wrong_arch)):
            self.item = release(data)
            with self.subTest(data=data[:6]), self.assertRaises(update.UpdateError):
                self.download([response(data)])

    def test_prepare_copies_old_executable_and_has_no_meeting_data(self):
        manifest = self.prepare()
        self.assertEqual(manifest.with_name("updater.exe").read_bytes(), self.old)
        plan = json.loads(manifest.read_text())
        self.assertEqual(plan["install_name"], self.target.name)
        self.assertEqual(plan["old_sha256"], hashlib.sha256(self.old).hexdigest())
        self.assertNotIn("transcripts", manifest.read_text())
        self.assertNotIn("config", manifest.read_text())

    def test_prepare_refuses_source_mode(self):
        candidate, _ = self.download()
        with patch.object(update.sys, "frozen", False, create=True):
            with self.assertRaisesRegex(update.UpdateError, "packaged"):
                update.prepare_install(candidate, self.target, self.item)

    def test_outside_candidate_is_rejected(self):
        outside = self.root / "candidate.exe"
        outside.write_bytes(self.data)
        with self.assertRaises(update.UpdateError):
            update._staged_path(outside, self.target)

    def test_success_waits_then_replaces_only_executable_and_keeps_backup(self):
        notes = self.root / "transcripts"
        notes.mkdir()
        transcript = notes / "meeting.md"
        transcript.write_text("keep these notes")
        settings = self.root / "tray-settings.json"
        settings.write_text("keep these settings")
        manifest = self.prepare()
        result, wait, restart = self.apply(manifest)
        self.assertEqual(result, 0)
        wait.assert_called_once()
        restart.assert_called_once_with(self.target)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(manifest.with_name("previous.exe.bak").read_bytes(), self.old)
        self.assertEqual(transcript.read_text(), "keep these notes")
        self.assertEqual(settings.read_text(), "keep these settings")

    def test_spawn_failure_rolls_back_previous_executable(self):
        manifest = self.prepare()
        result, _, restart = self.apply(manifest, side_effect=[OSError("launch denied"), None])
        self.assertEqual(result, 1)
        self.assertEqual(restart.call_count, 2)
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(manifest.with_name("previous.exe.bak").read_bytes(), self.old)

    def test_changed_current_executable_is_not_overwritten(self):
        manifest = self.prepare()
        self.target.write_bytes(executable(b"different"))
        result, _, restart = self.apply(manifest)
        self.assertEqual(result, 1)
        restart.assert_not_called()
        self.assertEqual(self.target.read_bytes(), executable(b"different"))

    def test_tampered_candidate_does_not_replace_original(self):
        manifest = self.prepare()
        manifest.with_name("candidate.exe").write_bytes(executable(b"bad"))
        with self.assertLogs("teams-caption-notes.update", level="ERROR"):
            result, _, restart = self.apply(manifest)
        self.assertEqual(result, 1)
        restart.assert_not_called()
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_manifest_cannot_target_another_directory(self):
        manifest = self.prepare()
        plan = json.loads(manifest.read_text())
        plan["install_name"] = "../another.exe"
        manifest.write_text(json.dumps(plan))
        with self.assertRaises(update.UpdateError):
            update._read_plan(manifest)

    def test_locked_executable_retries_without_terminating_processes(self):
        candidate, _ = self.download()
        with patch.object(update.os, "replace", side_effect=[PermissionError("locked"), None]) as move, patch.object(
                update.time, "sleep") as sleep:
            update._replace_with_retry(candidate, self.target, hashlib.sha256(self.old).hexdigest(),
                                       update.time.monotonic() + 30)
        self.assertEqual(move.call_count, 2)
        sleep.assert_called_once_with(0.5)
        self.assertTrue(candidate.exists())

    def test_failure_after_old_app_exits_restarts_unchanged_app(self):
        manifest = self.prepare()
        with patch.object(update, "_replace_with_retry", side_effect=update.UpdateError("locked")):
            result, _, restart = self.apply(manifest)
        self.assertEqual(result, 1)
        restart.assert_called_once_with(self.target)
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_lock_timeout_preserves_original(self):
        candidate, _ = self.download()
        with patch.object(update.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(update.UpdateError, "still in use"):
                update._replace_with_retry(candidate, self.target, hashlib.sha256(self.old).hexdigest(), 0)
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_helper_launch_waits_for_ready_before_authorizing(self):
        manifest = self.prepare()
        process = MagicMock()
        process.poll.return_value = None
        def spawned(*args, **kwargs):
            manifest.with_name("ready").write_text(update._sha256(manifest))
            return process
        with patch.object(update.subprocess, "Popen", side_effect=spawned) as spawn:
            self.assertIs(update.launch_installer(manifest), process)
        self.assertTrue(manifest.with_name("authorized").exists())
        self.assertFalse(manifest.with_name("cancelled").exists())
        self.assertEqual(spawn.call_args.args[0][1:], ["--apply-update", str(manifest)])
        self.assertNotIn("shell", spawn.call_args.kwargs)
        self.assertEqual(spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_slow_or_failed_helper_is_cancelled_not_installed_later(self):
        manifest = self.prepare()
        with patch.object(update.subprocess, "Popen") as spawn, patch.object(
                update, "HELPER_START_TIMEOUT", 0):
            with self.assertRaises(update.UpdateError):
                update.launch_installer(manifest)
        self.assertTrue(manifest.with_name("cancelled").exists())
        self.assertFalse(manifest.with_name("authorized").exists())
        plan = json.loads(manifest.read_text())
        with self.assertRaisesRegex(update.UpdateError, "cancelled"):
            update._await_authorization(manifest, plan, self.target)
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_helper_without_authorization_never_touches_target(self):
        manifest = self.prepare()
        plan = json.loads(manifest.read_text())
        with patch.object(update, "_process_created", return_value=123), patch.object(
                update, "HELPER_START_TIMEOUT", 0):
            with self.assertRaisesRegex(update.UpdateError, "did not authorize"):
                update._await_authorization(manifest, plan, self.target)
        self.assertTrue(manifest.with_name("ready").exists())
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_wait_verifies_process_identity_and_waits_without_killing(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 42
        kernel.WaitForSingleObject.return_value = 0
        with patch.object(update, "_kernel32", return_value=kernel), patch.object(
                update, "_process_details", return_value=(self.target, 123)):
            update._wait_for_old_process({"old_pid": 999, "old_created": 123}, self.target,
                                         update.time.monotonic() + 10)
        kernel.WaitForSingleObject.assert_called_once()
        kernel.CloseHandle.assert_called_once_with(42)
        kernel.TerminateProcess.assert_not_called()

    def test_reused_pid_is_not_waited_on_or_terminated(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 42
        with patch.object(update, "_kernel32", return_value=kernel), patch.object(
                update, "_process_details", return_value=(self.target, 456)):
            update._wait_for_old_process({"old_pid": 999, "old_created": 123}, self.target,
                                         update.time.monotonic() + 10)
        kernel.WaitForSingleObject.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(42)

    def test_old_app_wait_timeout_closes_handle_and_fails_safe(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 42
        kernel.WaitForSingleObject.return_value = 258
        with patch.object(update, "_kernel32", return_value=kernel), patch.object(
                update, "_process_details", return_value=(self.target, 123)):
            with self.assertRaisesRegex(update.UpdateError, "did not close"):
                update._wait_for_old_process({"old_pid": 999, "old_created": 123}, self.target,
                                             update.time.monotonic() + 10)
        kernel.CloseHandle.assert_called_once_with(42)
        self.assertEqual(self.target.read_bytes(), self.old)


if __name__ == "__main__":
    unittest.main()
