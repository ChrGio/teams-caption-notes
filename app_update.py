"""User-initiated, checksum-verified updates for the portable Windows executable.

Only GitHub's fixed public repository is contacted. Downloading never executes a
file. The tray owns consent and graceful shutdown; a copy of the *current* app
does the replacement after its old process exits. No meeting data is moved.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests


REPOSITORY = "ChrGio/teams-caption-notes"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
NETWORK_TIMEOUT = (10, 30)
INSTALL_TIMEOUT = 120
HELPER_START_TIMEOUT = 25
_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?\Z", re.ASCII)
_ASSET = re.compile(r"TeamsCaptionNotes(?:-v(\d+\.\d+(?:\.\d+)?))?\.exe\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_UPDATE_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_REDIRECT_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com"})
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "TeamsCaptionNotes-update-check"}


class UpdateError(RuntimeError):
    """An update could not be checked or safely installed."""


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    url: str
    sha256: str
    size: int
    html_url: str


def _version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match or len(value) > 32:
        raise UpdateError("The release version is not a supported numeric version.")
    return tuple(int(part or 0) for part in match.groups())


def _https_url(url: str, hosts: frozenset[str] | set[str]) -> None:
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in hosts or parts.username
                or parts.password or parts.port not in (None, 443) or parts.fragment
                or any(ord(char) < 33 for char in url)):
            raise ValueError("Untrusted URL")
    except (TypeError, ValueError) as exc:
        raise UpdateError("The update URL is not an approved HTTPS GitHub URL.") from exc


def _validate_release(release: Release) -> None:
    if _version(release.version) != _version(release.tag) or not release.tag.startswith("v"):
        raise UpdateError("The release tag and version do not match.")
    _https_url(release.url, {"github.com"})
    parts = urlsplit(release.url)
    prefix = f"/{REPOSITORY}/releases/download/{release.tag}/"
    name = parts.path[len(prefix):] if parts.path.startswith(prefix) else ""
    match = _ASSET.fullmatch(name)
    if (not match or parts.query or (match.group(1) and
            _version(match.group(1)) != _version(release.version))):
        raise UpdateError("The Windows executable is not from the expected repository/release.")
    if (not isinstance(release.sha256, str) or not _SHA256.fullmatch(release.sha256)
            or type(release.size) is not int or not 0 < release.size <= MAX_DOWNLOAD_BYTES):
        raise UpdateError("The release needs a valid GitHub SHA-256 digest and supported file size.")
    if release.html_url != f"https://github.com/{REPOSITORY}/releases/tag/{release.tag}":
        raise UpdateError("The release page is not from the expected repository.")


def _session() -> requests.Session:
    session = requests.Session()
    # Avoid implicit .netrc credentials; retain ordinary corporate proxy/CA setup.
    session.auth = _NoAuth()
    return session


class _NoAuth(requests.auth.AuthBase):
    def __call__(self, request):
        request.headers.pop("Authorization", None)
        return request


def _body(response, limit: int) -> bytes:
    data = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        data.extend(chunk)
        if len(data) > limit:
            raise UpdateError("GitHub returned a response larger than the allowed limit.")
    return bytes(data)


def check_for_update(current_version: str) -> Release | None:
    """Check the fixed public latest release; never download or launch an asset."""
    current = _version(current_version)
    try:
        with _session() as session:
            with session.get(LATEST_URL, headers=_HEADERS, timeout=NETWORK_TIMEOUT,
                             allow_redirects=False, stream=True) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise UpdateError(f"GitHub update check returned HTTP {response.status_code}.")
                info = json.loads(_body(response, MAX_METADATA_BYTES))
    except (requests.RequestException, ValueError) as exc:
        raise UpdateError("Could not read the latest GitHub release. Please try again later.") from exc
    if not isinstance(info, dict):
        raise UpdateError("GitHub returned invalid release information.")
    if info.get("draft") is not False or info.get("prerelease") is not False:
        return None
    tag = info.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("The latest release does not have a supported version tag.")
    if _version(tag) <= current:
        return None
    assets = info.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The release does not contain an asset list.")
    candidates = [asset for asset in assets if isinstance(asset, dict)
                  and isinstance(asset.get("name"), str) and _ASSET.fullmatch(asset["name"])]
    if len(candidates) != 1:
        raise UpdateError("The release must contain exactly one supported Windows executable.")
    asset = candidates[0]
    digest = asset.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise UpdateError("GitHub has not supplied a SHA-256 digest; automatic installation is disabled.")
    release = Release(tag[1:], tag, asset.get("browser_download_url"), digest[7:].lower(),
                      asset.get("size"), info.get("html_url"))
    _validate_release(release)
    if urlsplit(release.url).path.rsplit("/", 1)[-1] != asset["name"]:
        raise UpdateError("The release asset name does not match its download URL.")
    return release


def _target(install_path: Path) -> Path:
    path = Path(install_path).absolute()
    if not _ASSET.fullmatch(path.name) or path.is_symlink() or not path.is_file():
        raise UpdateError("Updates require an existing TeamsCaptionNotes Windows executable.")
    # Resolve only after rejecting a redirected executable itself.
    return path.resolve(strict=True)


def _update_directory(target: Path) -> Path:
    base = target.parent / ".updates"
    base.mkdir(exist_ok=True)
    if base.resolve() != base or base.is_symlink():
        raise UpdateError("The update staging folder cannot be a redirected folder.")
    path = base / uuid.uuid4().hex
    path.mkdir()
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_candidate(path: Path, release: Release) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != release.size:
        raise UpdateError("The downloaded executable has an unexpected file size or path.")
    if _sha256(path) != release.sha256:
        raise UpdateError("The downloaded executable failed its SHA-256 check. It will not run.")
    with path.open("rb") as source:
        header = source.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise UpdateError("The download is not a Windows executable.")
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset < 64 or offset > release.size - 26:
            raise UpdateError("The download has an invalid Windows executable header.")
        source.seek(offset)
        pe = source.read(26)
        if (pe[:4] != b"PE\x00\x00" or struct.unpack_from("<H", pe, 4)[0] != 0x8664
                or struct.unpack_from("<H", pe, 24)[0] != 0x20B):
            raise UpdateError("The download is not a 64-bit Windows executable.")


def download_update(release: Release, install_path: Path) -> Path:
    """Stage and verify an executable without running it or changing the app."""
    _validate_release(release)
    target = _target(install_path)
    folder = _update_directory(target)
    candidate = folder / "candidate.exe"
    url = release.url
    try:
        with _session() as session:
            for _ in range(6):
                _https_url(url, _REDIRECT_HOSTS)
                with session.get(url, headers={"User-Agent": _HEADERS["User-Agent"],
                                               "Accept": "application/octet-stream"},
                                 timeout=NETWORK_TIMEOUT, allow_redirects=False, stream=True) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise UpdateError("GitHub returned an empty download redirect.")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise UpdateError(f"GitHub download returned HTTP {response.status_code}.")
                    count = 0
                    with candidate.open("xb") as output:
                        for block in response.iter_content(chunk_size=128 * 1024):
                            count += len(block)
                            if count > release.size or count > MAX_DOWNLOAD_BYTES:
                                raise UpdateError("The download exceeds the expected file size.")
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                    _validate_candidate(candidate, release)
                    return candidate
            raise UpdateError("GitHub returned too many download redirects.")
    except (requests.RequestException, OSError) as exc:
        raise UpdateError("Could not download the update. The installed app was not changed.") from exc


def _kernel32():
    if os.name != "nt":
        raise UpdateError("Automatic installation is only supported on Windows.")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def _process_details(kernel, handle) -> tuple[Path, int]:
    from ctypes import wintypes
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    created, exited, kerneltime, usertime = (wintypes.FILETIME() for _ in range(4))
    if (not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            or not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kerneltime), ctypes.byref(usertime))):
        raise UpdateError("Could not verify the running app process.")
    return Path(buffer.value).resolve(), (created.dwHighDateTime << 32) | created.dwLowDateTime


def _process_created(pid: int, expected: Path) -> int:
    kernel = _kernel32()
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise UpdateError("Could not inspect the running app before updating.")
    try:
        path, created = _process_details(kernel, handle)
        if path != expected:
            raise UpdateError("The running app is not the executable selected for updating.")
        return created
    finally:
        kernel.CloseHandle(handle)


def _wait_for_old_process(plan: dict, target: Path, deadline: float) -> None:
    kernel = _kernel32()
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, plan["old_pid"])
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: process no longer exists
            return
        raise UpdateError("Could not wait safely for the previous app to close.")
    try:
        path, created = _process_details(kernel, handle)
        if path != target or created != plan["old_created"]:
            # PID was reused: it is not the app we are updating and must not be touched.
            return
        milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
        result = kernel.WaitForSingleObject(handle, milliseconds)
        if result != 0:
            raise UpdateError("The app did not close in time. Close it and try the update again.")
    finally:
        kernel.CloseHandle(handle)


def _staged_path(candidate: Path, target: Path) -> Path:
    path = Path(candidate).absolute()
    folder = path.parent
    if (path.name != "candidate.exe" or not _UPDATE_ID.fullmatch(folder.name)
            or folder.parent != target.parent / ".updates" or path.resolve() != path
            or folder.resolve() != folder or folder.parent.resolve() != folder.parent):
        raise UpdateError("The candidate is not in this app's private update staging folder.")
    return path


def prepare_install(candidate: Path, install_path: Path, release: Release) -> Path:
    """Prepare a local helper/manifest; caller still owns consent and shutdown."""
    _validate_release(release)
    target = _target(install_path)
    if not getattr(sys, "frozen", False) or Path(sys.executable).resolve() != target:
        raise UpdateError("Run the packaged executable to install updates automatically.")
    staged = _staged_path(candidate, target)
    _validate_candidate(staged, release)
    old_hash = _sha256(target)
    created = _process_created(os.getpid(), target)
    helper = staged.with_name("updater.exe")
    if helper.exists():
        raise UpdateError("This staged update has already been prepared. Check for updates again.")
    with target.open("rb") as source, helper.open("xb") as destination:
        shutil.copyfileobj(source, destination)
        destination.flush()
        os.fsync(destination.fileno())
    if _sha256(helper) != old_hash:
        raise UpdateError("Could not verify the update helper copy.")
    plan = {"schema": 1, "install_name": target.name, "version": release.version,
            "tag": release.tag, "url": release.url, "sha256": release.sha256,
            "size": release.size, "html_url": release.html_url, "old_sha256": old_hash,
            "old_pid": os.getpid(), "old_created": created}
    manifest = staged.with_name("install.json")
    with manifest.open("x", encoding="utf-8") as output:
        json.dump(plan, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return manifest


def _read_plan(manifest: Path) -> tuple[dict, Path, Path, Release]:
    path = Path(manifest).absolute()
    folder = path.parent
    if (path.name != "install.json" or not _UPDATE_ID.fullmatch(folder.name)
            or folder.parent.name != ".updates" or path.resolve() != path
            or folder.resolve() != folder or folder.parent.resolve() != folder.parent
            or path.stat().st_size > 16 * 1024):
        raise UpdateError("Invalid update manifest location.")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(plan, dict) or plan.get("schema") != 1
            or not isinstance(plan.get("install_name"), str)
            or not _ASSET.fullmatch(plan["install_name"])
            or not isinstance(plan.get("old_sha256"), str)
            or not _SHA256.fullmatch(plan["old_sha256"])
            or type(plan.get("old_pid")) is not int or not 0 < plan["old_pid"] <= 0xFFFFFFFF
            or type(plan.get("old_created")) is not int or plan["old_created"] <= 0):
        raise UpdateError("Invalid update manifest contents.")
    target = _target(folder.parent.parent / plan["install_name"])
    candidate = _staged_path(folder / "candidate.exe", target)
    release = Release(**{key: plan[key] for key in ("version", "tag", "url", "sha256", "size", "html_url")})
    _validate_release(release)
    helper = folder / "updater.exe"
    if helper.resolve() != helper or _sha256(helper) != plan["old_sha256"]:
        raise UpdateError("The update helper does not match the installed app.")
    _validate_candidate(candidate, release)
    return plan, target, candidate, release


def _independent_environment() -> dict:
    environment = os.environ.copy()
    # A one-file PyInstaller child must not depend on its parent's temporary files.
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def launch_installer(manifest: Path) -> subprocess.Popen:
    _read_plan(manifest)
    manifest = Path(manifest).resolve()
    helper = manifest.with_name("updater.exe")
    ready, authorized, cancelled = (helper.with_name(name) for name in
                                     ("ready", "authorized", "cancelled"))
    if any(path.exists() for path in (ready, authorized, cancelled)):
        raise UpdateError("This prepared installer was already started. Check for updates again.")
    process = subprocess.Popen([str(helper), "--apply-update", str(manifest)],
                               cwd=str(helper.parent), env=_independent_environment(),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + HELPER_START_TIMEOUT
    checksum = _sha256(manifest)
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise UpdateError("The updater could not start. See the staged update.log file.")
            if ready.is_file() and ready.read_text(encoding="ascii") == checksum:
                _marker(authorized, checksum)
                return process
            time.sleep(0.1)
        raise UpdateError("The updater did not become ready in time. The app was not replaced.")
    except Exception:
        _marker(cancelled, checksum)
        raise


def _marker(path: Path, text: str) -> None:
    if path.exists():
        raise UpdateError("An update handshake marker already exists.")
    pending = path.with_name(path.name + ".pending-" + uuid.uuid4().hex)
    with pending.open("x", encoding="ascii") as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())
    # Publish only after the whole marker is durable; especially important for
    # authorization, which must not become visible before fsync succeeds.
    os.rename(pending, path)


def _await_authorization(manifest: Path, plan: dict, target: Path) -> None:
    folder = manifest.parent
    checksum = _sha256(manifest)
    cancelled, authorized = folder / "cancelled", folder / "authorized"
    if cancelled.exists():
        raise UpdateError("The launching app cancelled this update.")
    if _process_created(plan["old_pid"], target) != plan["old_created"]:
        raise UpdateError("The original app process no longer matches this installer.")
    _marker(folder / "ready", checksum)
    deadline = time.monotonic() + HELPER_START_TIMEOUT
    while time.monotonic() < deadline:
        if cancelled.exists():
            raise UpdateError("The launching app cancelled this update.")
        if authorized.is_file() and authorized.read_text(encoding="ascii") == checksum:
            return
        time.sleep(0.1)
    raise UpdateError("The launching app did not authorize this installation.")


def _replace_with_retry(candidate: Path, target: Path, expected_hash: str, deadline: float) -> None:
    while True:
        if _sha256(target) != expected_hash:
            raise UpdateError("The installed app changed while this update was waiting.")
        try:
            os.replace(candidate, target)
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise UpdateError("The executable is still in use. Close other copies and try again.") from exc
            time.sleep(0.5)


def _restart(target: Path) -> None:
    subprocess.Popen([str(target)], cwd=str(target.parent), env=_independent_environment(),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def apply_update(manifest: Path) -> int:
    """Helper entry point. No killing apps, elevation, shell scripts, or deletions."""
    log = logging.getLogger("teams-caption-notes.update")
    handler = None
    replaced = False
    old_closed = False
    target = backup = None
    try:
        plan, target, candidate, release = _read_plan(manifest)
        folder = candidate.parent
        if (not getattr(sys, "frozen", False)
                or Path(sys.executable).resolve() != folder / "updater.exe"
                or plan["old_pid"] == os.getpid()):
            raise UpdateError("Update application must run from its prepared helper executable.")
        handler = logging.FileHandler(folder / "update.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        _await_authorization(Path(manifest).resolve(), plan, target)
        log.info("Waiting for old app to exit before installing version %s", release.version)
        deadline = time.monotonic() + INSTALL_TIMEOUT
        _wait_for_old_process(plan, target, deadline)
        old_closed = True
        if _sha256(target) != plan["old_sha256"]:
            raise UpdateError("The installed executable changed; this update was not applied.")
        backup = folder / "previous.exe.bak"
        with target.open("rb") as source, backup.open("xb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        if _sha256(backup) != plan["old_sha256"]:
            raise UpdateError("The original executable backup could not be verified.")
        _validate_candidate(candidate, release)
        _replace_with_retry(candidate, target, plan["old_sha256"], deadline)
        replaced = True
        if _sha256(target) != release.sha256:
            raise UpdateError("The installed update failed its final checksum verification.")
        _restart(target)
        log.info("Installed version %s and requested app restart. Original backup: %s",
                 release.version, backup.name)
        return 0
    except Exception:
        log.exception("Update failed; meeting files and settings were not changed")
        if replaced and backup is not None and target is not None:
            try:
                # Preserve the backup too; a failed launch must not discard the old app.
                if _sha256(backup) != plan["old_sha256"]:
                    raise UpdateError("The rollback backup no longer matches the original app.")
                restore = backup.with_name("restore.exe")
                with backup.open("rb") as source, restore.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
                _replace_with_retry(restore, target, _sha256(target), time.monotonic() + 30)
                log.info("Restored the previous app after installation/restart failed")
                _restart(target)
            except Exception:
                log.exception("Rollback/restart failed; original executable remains in %s", backup)
        elif old_closed and target is not None:
            try:
                if _sha256(target) == plan["old_sha256"]:
                    _restart(target)
                    log.info("Requested restart of unchanged original app after update failed")
            except Exception:
                log.exception("The unchanged original app could not be restarted")
        return 1
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()
