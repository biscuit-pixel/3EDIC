"""Updater against a mocked GitHub (no network)."""
import hashlib
import json

import httpx
import pytest

from edic import updater as U

API = "https://api.github.com/repos/biscuit-pixel/3EDIC/releases/latest"
EXE = b"MZ" + b"installer" * 1000


def release(tag="v0.2.0", assets=None, prerelease=False, body="Faster everything.", host="https://github.com"):
    exe = f"3EDIC-Setup-{tag.lstrip('v')}.exe"
    dmg = f"3EDIC-{tag.lstrip('v')}-macos-arm64.dmg"
    assets = assets if assets is not None else [exe, dmg, "SHA256SUMS.txt"]
    return {"tag_name": tag, "prerelease": prerelease, "draft": False, "body": body, "html_url": "https://github.com/biscuit-pixel/3EDIC/releases/tag/" + tag,
            "assets": [{"name": n, "size": len(EXE), "browser_download_url": f"{host}/dl/{n}"} for n in assets]}


def make(rel, files=None, tmp_path=None, **kw):
    files = files if files is not None else {}
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if str(req.url) == API:
            return httpx.Response(404) if rel is None else httpx.Response(200, json=rel)
        name = str(req.url).rsplit("/", 1)[-1]
        if name in files:
            return httpx.Response(200, content=files[name], headers={"content-length": str(len(files[name]))})
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return U.Updater(tmp_path, client=client, api_url=API, **kw), calls


def sums(name, data):
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


def test_version_ordering():
    v = U.parse_version
    assert v("0.2.0") > v("0.1.9") > v("0.1.0") and v("v1.0.0") > v("0.99.99")
    assert v("0.2.0") > v("0.2.0-beta1")                      # a final release beats its own pre-release
    assert v("0.2.0-beta2") > v("0.2.0-beta1")
    with pytest.raises(ValueError):
        v("latest")


def test_asset_per_platform():
    assert U.asset_name_for("0.2.0", "win32") == "3EDIC-Setup-0.2.0.exe"
    assert U.asset_name_for("0.2.0", "darwin", "arm64") == "3EDIC-0.2.0-macos-arm64.dmg"
    assert U.asset_name_for("0.2.0", "darwin", "x86_64") == "3EDIC-0.2.0-macos-x86_64.dmg"
    assert U.asset_name_for("0.2.0", "linux") is None


def test_newer_release_is_offered_and_installable_when_packaged(tmp_path):
    up, _ = make(release(), tmp_path=tmp_path, current="0.1.0", plat="win32", frozen=True)
    r = up.check()
    assert r["available"] and r["latest"] == "0.2.0" and r["can_install"] and r["asset"] == "3EDIC-Setup-0.2.0.exe"
    assert "Faster" in r["notes"]


@pytest.mark.parametrize("current,rel,available", [
    ("0.2.0", release("v0.2.0"), False),                          # same version
    ("0.3.0", release("v0.2.0"), False),                          # we are newer
    ("0.1.0", release("v0.2.0", prerelease=True), False),         # pre-releases are not pushed to users
    ("0.1.0", None, False),                                       # nothing published yet
])
def test_no_update_cases(tmp_path, current, rel, available):
    up, _ = make(rel, tmp_path=tmp_path, current=current, plat="win32", frozen=True)
    assert up.check()["available"] is available


def test_running_from_source_is_told_not_touched(tmp_path):
    up, _ = make(release(), tmp_path=tmp_path, current="0.1.0", plat="win32", frozen=False)
    r = up.check()
    assert r["available"] and not r["can_install"] and r["reason"] == "not_installed_app"


def test_release_without_installer_for_this_computer(tmp_path):
    up, _ = make(release(assets=["3EDIC-0.2.0-macos-arm64.dmg", "SHA256SUMS.txt"]), tmp_path=tmp_path, current="0.1.0", plat="win32", frozen=True)
    r = up.check()
    assert r["available"] and not r["can_install"] and r["reason"] == "no_asset"
    up, _ = make(release(assets=["3EDIC-Setup-0.2.0.exe"]), tmp_path=tmp_path, current="0.1.0", plat="win32", frozen=True)
    assert up.check(force=True)["reason"] == "no_asset"           # an installer without checksums is not installed


def test_offline_is_not_an_error_and_checks_are_cached(tmp_path):
    def boom(req):
        raise httpx.ConnectError("no network")
    up = U.Updater(tmp_path, client=httpx.Client(transport=httpx.MockTransport(boom)), api_url=API, current="0.1.0")
    r = up.check()
    assert r["available"] is False and "ConnectError" in r["error"]
    up2, calls = make(release(), tmp_path=tmp_path / "c", current="0.1.0", plat="win32", frozen=True)
    up2.check(); up2.check(); up2.check()
    assert len(calls) == 1                                        # six hours between automatic checks
    up2.check(force=True)
    assert len(calls) == 2


def test_download_verifies_checksum(tmp_path):
    files = {"3EDIC-Setup-0.2.0.exe": EXE, "SHA256SUMS.txt": sums("3EDIC-Setup-0.2.0.exe", EXE)}
    up, _ = make(release(), files, tmp_path, current="0.1.0", plat="win32", frozen=True)
    seen = []
    path = up.download_and_verify(up.check(), progress=lambda d, t, n: seen.append((d, t)))
    assert path.read_bytes() == EXE and seen and seen[-1][0] == len(EXE)


def test_tampered_download_is_rejected_and_not_kept(tmp_path):
    files = {"3EDIC-Setup-0.2.0.exe": EXE + b"evil", "SHA256SUMS.txt": sums("3EDIC-Setup-0.2.0.exe", EXE)}
    up, _ = make(release(), files, tmp_path, current="0.1.0", plat="win32", frozen=True)
    with pytest.raises(U.UpdateError, match="checksum"):
        up.download_and_verify(up.check())
    assert not list((tmp_path / "updates").glob("3EDIC-*"))


def test_missing_checksum_line_is_rejected(tmp_path):
    files = {"3EDIC-Setup-0.2.0.exe": EXE, "SHA256SUMS.txt": sums("something-else.exe", EXE)}
    up, _ = make(release(), files, tmp_path, current="0.1.0", plat="win32", frozen=True)
    with pytest.raises(U.UpdateError, match="no checksum"):
        up.download_and_verify(up.check())


def test_downloads_only_from_github(tmp_path):
    files = {"3EDIC-Setup-0.2.0.exe": EXE, "SHA256SUMS.txt": sums("3EDIC-Setup-0.2.0.exe", EXE)}
    up, calls = make(release(host="https://evil.example"), files, tmp_path, current="0.1.0", plat="win32", frozen=True)
    info = up.check()
    with pytest.raises(U.UpdateError, match="GitHub"):
        up.download_and_verify(info)
    assert not any("evil.example" in c for c in calls)            # nothing was even requested from there
    for ok, url in ((True, "https://github.com/x"), (True, "https://objects.githubusercontent.com/x"), (False, "http://github.com/x"),
                    (False, "https://github.com.evil.example/x"), (False, "https://notgithub.com/x")):
        assert U.host_allowed(url) is ok, url


def test_odd_asset_names_and_huge_files_are_refused(tmp_path):
    up, _ = make(release(), {}, tmp_path, current="0.1.0", plat="win32", frozen=True)
    with pytest.raises(U.UpdateError, match="file name"):
        up.download_and_verify({"can_install": True, "asset": "../../evil.exe"})
    big = {"3EDIC-Setup-0.2.0.exe": EXE, "SHA256SUMS.txt": sums("3EDIC-Setup-0.2.0.exe", EXE)}
    up2, _ = make(release(), big, tmp_path / "b", current="0.1.0", plat="win32", frozen=True)
    info = up2.check()
    info["size"] = U.MAX_DOWNLOAD + 1
    big["3EDIC-Setup-0.2.0.exe"] = EXE                             # content-length is what the server says
    import edic.updater as m
    old = m.MAX_DOWNLOAD
    m.MAX_DOWNLOAD = len(EXE) - 1
    try:
        with pytest.raises(U.UpdateError, match="large"):
            up2.download_and_verify(info)
    finally:
        m.MAX_DOWNLOAD = old


def test_windows_runs_the_installer_silently_and_asks_for_relaunch(tmp_path):
    up, _ = make(release(), tmp_path=tmp_path, current="0.1.0", plat="win32", frozen=True)
    cmd = up.apply(tmp_path / "3EDIC-Setup-0.2.0.exe", dry_run=True)
    assert cmd[1:] == ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]


def test_mac_swaps_the_app_in_place_or_falls_back_to_the_disk_image(tmp_path, monkeypatch):
    app = tmp_path / "Applications" / "3EDIC.app"
    app.mkdir(parents=True)
    up, _ = make(release(), tmp_path=tmp_path, current="0.1.0", plat="darwin", frozen=True, machine="arm64")
    monkeypatch.setattr(U, "current_app_bundle", lambda: app)
    cmd = up.apply(tmp_path / "x.dmg", dry_run=True)
    assert cmd[0] == "/bin/bash" and cmd[-1] == str(app)
    script = (tmp_path / "updates" / "swap.sh").read_text()
    assert "kill -0" in script and 'hdiutil attach' in script and 'mv "$APP.new" "$APP"' in script
    monkeypatch.setattr(U, "current_app_bundle", lambda: None)
    assert up.apply(tmp_path / "x.dmg", dry_run=True)[0] == "open"


@pytest.mark.parametrize("plat,machine", [("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64"), ("darwin", "aarch64")])
@pytest.mark.parametrize("version", ["0.1.0", "1.20.3", "0.2.0-beta1", "0.0.0.dev0"])
def test_every_file_name_the_build_produces_passes_the_safety_filter(plat, machine, version):
    """Regression: the Intel Mac name contains an underscore and was refused by the filter."""
    name = U.asset_name_for(version, plat, machine)
    assert name and U.ASSET_RE.match(name), name


def test_safety_filter_still_blocks_path_tricks():
    for bad in ("../3EDIC-Setup-1.0.0.exe", "3EDIC-Setup-1.0.0.exe/../x", "3EDIC/../x.exe", "evil.exe", "3EDIC-x.sh", "3EDIC- x.exe", "3EDIC-a\\b.exe"):
        assert not U.ASSET_RE.match(bad), bad
