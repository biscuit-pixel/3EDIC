"""Update check and one-click update from GitHub Releases.

How you ship an update:   git tag v0.2.0 && git push origin v0.2.0   (the release workflow does the rest)
How an installed app gets it: it asks GitHub for the latest release, compares versions, and on "Update now"
downloads the installer for this computer, verifies its SHA-256 against the release's SHA256SUMS.txt, then hands
over to the installer (Windows) or a small swap script (macOS) and quits so the files can be replaced.

Only files from this project's own GitHub releases are ever downloaded. Integrity rests on HTTPS + the checksum
file published by the same release (so: keep two-factor authentication on the GitHub account). Code signing is a
planned extra layer (see TODO.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import __version__

GITHUB_REPO = "biscuit-pixel/3EDIC"
CHECK_EVERY = 6 * 3600                       # seconds between automatic checks
MAX_DOWNLOAD = 1_500_000_000                 # refuse anything bigger than 1.5 GB
ALLOWED_HOSTS = ("github.com", "api.github.com", "githubusercontent.com")      # also matches *.githubusercontent.com
ASSET_RE = re.compile(r"^3EDIC-[0-9A-Za-z._+\-]+\.(exe|dmg)$")      # underscores: macos-x86_64


class UpdateError(RuntimeError):
    pass


def parse_version(v: str):
    """'v1.2.3' / '1.2.3-beta1' -> comparable tuple. A final release sorts after its own pre-releases."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[.\-+]?([0-9A-Za-z.\-]+))?$", v.strip())
    if not m:
        raise ValueError(f"not a version: {v!r}")
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    return (major, minor, patch, 1 if pre is None else 0, pre or "")


def host_allowed(url: str, extra_hosts: tuple = ()) -> bool:
    u = urlparse(url)
    h = (u.hostname or "").lower()
    if u.scheme != "https" and h not in extra_hosts:
        return False
    return h in extra_hosts or any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS)


def asset_name_for(version: str, plat: str = sys.platform, machine: str | None = None) -> str | None:
    machine = (machine or platform.machine()).lower()
    if plat == "win32":
        return f"3EDIC-Setup-{version}.exe"
    if plat == "darwin":
        return f"3EDIC-{version}-macos-{'arm64' if machine in ('arm64', 'aarch64') else 'x86_64'}.dmg"
    return None


def installer_command(path: Path, plat: str = sys.platform) -> list[str]:
    """Silent install that closes the running app and starts the new one afterwards (see the Inno script)."""
    if plat == "win32":
        return [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]
    raise UpdateError("no silent installer on this platform")


MAC_SWAP_SCRIPT = r"""#!/bin/bash
# Replace the installed app with the one from the downloaded disk image, then start it.
PID="$1"; DMG="$2"; APP="$3"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
MNT="$(mktemp -d)"
if ! hdiutil attach "$DMG" -mountpoint "$MNT" -nobrowse -quiet; then open "$DMG"; exit 1; fi
if [ -d "$MNT/3EDIC.app" ] && rm -rf "$APP.new" && cp -R "$MNT/3EDIC.app" "$APP.new"; then
  rm -rf "$APP.old"; mv "$APP" "$APP.old" && mv "$APP.new" "$APP" && rm -rf "$APP.old"
  hdiutil detach "$MNT" -quiet; open "$APP"
else
  hdiutil detach "$MNT" -quiet; open "$DMG"
fi
"""


def current_app_bundle() -> Path | None:
    """/Applications/3EDIC.app when running as a packaged macOS app."""
    exe = Path(sys.executable).resolve()
    return exe.parents[2] if sys.platform == "darwin" and getattr(sys, "frozen", False) and exe.parents[2].suffix == ".app" else None


class Updater:
    def __init__(self, data_dir: Path, current: str = __version__, repo: str = GITHUB_REPO, client: httpx.Client | None = None,
                 plat: str = sys.platform, machine: str | None = None, frozen: bool | None = None, api_url: str | None = None):
        self.dir = Path(data_dir) / "updates"
        self.current, self.repo, self.plat, self.machine = current, repo, plat, machine
        self.frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        self.api_url = api_url or os.environ.get("EDIC_UPDATE_API") or f"https://api.github.com/repos/{repo}/releases/latest"
        self.extra_hosts = (urlparse(self.api_url).hostname,) if os.environ.get("EDIC_UPDATE_API") or api_url else ()
        self.client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20, read=120),
                                             headers={"User-Agent": f"3EDIC/{current}", "Accept": "application/vnd.github+json"})
        self.cache_path = self.dir / "last-check.json"

    # -------------------------------------------------------------- checking
    def check(self, force: bool = False) -> dict:
        """-> what the UI shows. Never raises: a failed check is reported as such (no internet is normal)."""
        if not force:
            try:
                c = json.loads(self.cache_path.read_text())
                if time.time() - c["at"] < CHECK_EVERY and c["current"] == self.current:
                    return c["result"]
            except (OSError, ValueError, KeyError):
                pass
        try:
            r = self.client.get(self.api_url)
            if r.status_code == 404:                         # no release published yet
                res = self._result(None)
            else:
                r.raise_for_status()
                res = self._result(r.json())
        except (httpx.HTTPError, ValueError, KeyError) as e:
            return {"current": self.current, "available": False, "error": f"{type(e).__name__}: {e}"[:200]}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps({"at": time.time(), "current": self.current, "result": res}))
        except OSError:
            pass
        return res

    def _result(self, rel: dict | None) -> dict:
        base = {"current": self.current, "latest": None, "available": False, "notes": "", "page": f"https://github.com/{self.repo}/releases",
                "asset": None, "size": None, "can_install": False, "reason": None}
        if not rel:
            return base
        tag = rel.get("tag_name", "")
        try:
            newer = parse_version(tag) > parse_version(self.current) and not rel.get("prerelease") and not rel.get("draft")
        except ValueError:
            return base
        latest = tag.lstrip("v")
        want = asset_name_for(latest, self.plat, self.machine)
        assets = {a["name"]: a for a in rel.get("assets", [])}
        reason = None
        if not self.frozen:
            reason = "not_installed_app"                     # running from source: tell, do not touch
        elif want is None:
            reason = "unsupported_platform"
        elif want not in assets or "SHA256SUMS.txt" not in assets:
            reason = "no_asset"
        elif self.plat == "darwin" and current_app_bundle() and not os.access(current_app_bundle().parent, os.W_OK):
            reason = "not_writable"
        return {**base, "latest": latest, "available": bool(newer), "notes": (rel.get("body") or "")[:6000],
                "page": rel.get("html_url") or base["page"], "asset": want if want in assets else None,
                "size": assets.get(want, {}).get("size"), "can_install": bool(newer) and reason is None, "reason": reason}

    # ------------------------------------------------------------ installing
    def download_and_verify(self, info: dict, progress=None, cancelled=lambda: False) -> Path:
        """Download the installer named in `info` and check it against the release's SHA256SUMS.txt."""
        if not info.get("can_install") or not info.get("asset"):
            raise UpdateError("this update cannot be installed automatically")
        name = info["asset"]
        if not ASSET_RE.match(name):
            raise UpdateError("unexpected file name in the release")
        rel = self.client.get(self.api_url)
        rel.raise_for_status()
        assets = {a["name"]: a["browser_download_url"] for a in rel.json().get("assets", [])}
        for n in (name, "SHA256SUMS.txt"):
            if n not in assets or not host_allowed(assets[n], self.extra_hosts):
                raise UpdateError(f"{n} is not available from GitHub")
        sums = self.client.get(assets["SHA256SUMS.txt"])
        sums.raise_for_status()
        expected = next((line.split()[0].lower() for line in sums.text.splitlines() if len(line.split()) == 2 and line.split()[1].lstrip("*") == name), None)
        if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError("the release has no checksum for this file")

        self.dir.mkdir(parents=True, exist_ok=True)
        dest, part = self.dir / name, self.dir / (name + ".part")
        h, done = hashlib.sha256(), 0
        with self.client.stream("GET", assets[name]) as r:
            r.raise_for_status()
            if not host_allowed(str(r.url), self.extra_hosts):
                raise UpdateError("the download was redirected somewhere unexpected")
            total = int(r.headers.get("content-length") or info.get("size") or 0)
            if total > MAX_DOWNLOAD:
                raise UpdateError("the file is unexpectedly large")
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if cancelled():
                        part.unlink(missing_ok=True)
                        raise UpdateError("cancelled")
                    done += len(chunk)
                    if done > MAX_DOWNLOAD:
                        raise UpdateError("the file is unexpectedly large")
                    h.update(chunk)
                    f.write(chunk)
                    if progress:
                        progress(done, total or done, name)
        if h.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise UpdateError("the downloaded file does not match its checksum; nothing was installed")
        os.replace(part, dest)
        return dest

    def apply(self, installer: Path, dry_run: bool = False) -> list[str]:
        """Hand over to the platform's installer. The caller quits the app right afterwards."""
        if self.plat == "win32":
            cmd = installer_command(installer, "win32")
        elif self.plat == "darwin":
            bundle = current_app_bundle()
            if bundle is None or not os.access(bundle.parent, os.W_OK):
                cmd = ["open", str(installer)]               # cannot swap in place: show the disk image instead
            else:
                self.dir.mkdir(parents=True, exist_ok=True)
                script = self.dir / "swap.sh"
                script.write_text(MAC_SWAP_SCRIPT)
                script.chmod(0o755)
                cmd = ["/bin/bash", str(script), str(os.getpid()), str(installer), str(bundle)]
        else:
            raise UpdateError("automatic updates are for the installed Windows and macOS apps")
        if not dry_run:
            kw = {"close_fds": True, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if self.plat == "win32":
                kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            else:
                kw["start_new_session"] = True
            subprocess.Popen(cmd, **kw)
        return cmd
