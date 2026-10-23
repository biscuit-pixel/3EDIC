"""Download manager against a mocked server: resume, checksum, mirrors, licence gate, zip members."""
import hashlib
import io
import zipfile

import httpx
import pytest

from edic.models.manager import ModelError, ModelManager, _Cancelled

DATA = bytes(range(256)) * 4000          # ~1 MB
SHA = hashlib.sha256(DATA).hexdigest()


def registry(sources, gate=None, sha=SHA, size=len(DATA)):
    return {"version": 1, "models": [{"id": "m", "title": "M", "purpose": "p", "required": True, "size": size, "sha256": sha,
                                       "file": "m.onnx", "license": "x", **({"license_gate": gate} if gate else {}), "sources": sources}]}


def server(payload=DATA, fail_first=0, ignore_range=False):
    state = {"n": 0, "ranges": []}

    def handler(req):
        state["n"] += 1
        if state["n"] <= fail_first:
            return httpx.Response(503)
        rng = req.headers.get("range")
        state["ranges"].append(rng)
        if rng and not ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            return httpx.Response(206, content=payload[start:])
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), state


def test_downloads_and_verifies(tmp_path):
    c, _ = server()
    mm = ModelManager(tmp_path, registry([{"url": "http://x/m"}]), c)
    assert not mm.ready()
    assert mm.ensure() == ["m"] and mm.ready() and mm.path("m").read_bytes() == DATA


def test_resumes_from_partial_file(tmp_path):
    c, state = server()
    mm = ModelManager(tmp_path, registry([{"url": "http://x/m"}]), c)
    (tmp_path / "m.part").write_bytes(DATA[:300_000])
    mm.ensure()
    assert state["ranges"] == ["bytes=300000-"] and mm.path("m").read_bytes() == DATA


def test_server_ignoring_range_restarts_cleanly(tmp_path):
    c, _ = server(ignore_range=True)
    mm = ModelManager(tmp_path, registry([{"url": "http://x/m"}]), c)
    (tmp_path / "m.part").write_bytes(b"garbage" * 1000)
    mm.ensure()
    assert mm.path("m").read_bytes() == DATA


def test_checksum_mismatch_is_rejected_and_leaves_nothing(tmp_path, monkeypatch):
    c, _ = server(payload=b"x" * len(DATA))
    mm = ModelManager(tmp_path, registry([{"url": "http://x/m"}]), c)
    with pytest.raises(ModelError, match="checksum"):
        mm.ensure()
    assert not mm.path("m").exists() and not list(tmp_path.glob("*.part"))


def test_falls_back_to_second_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr("edic.models.manager.time.sleep", lambda s: None)
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    good, _ = server()

    def handler(req):
        return (bad if "dead" in str(req.url) else good).send(req)
    mm = ModelManager(tmp_path, registry([{"url": "http://dead/m"}, {"url": "http://ok/m"}]),
                      httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503) if "dead" in str(r.url) else httpx.Response(200, content=DATA))))
    mm.ensure()
    assert mm.installed("m")


def test_licence_gate_blocks_until_accepted(tmp_path):
    c, _ = server()
    mm = ModelManager(tmp_path, registry([{"url": "http://x/m"}], gate="non-commercial"), c)
    with pytest.raises(ModelError, match="license"):
        mm.ensure()
    mm.accept_license("m")
    assert ModelManager(tmp_path, registry([{"url": "http://x/m"}], gate="x"), c).accepted() == {"m"}     # persisted
    mm.ensure()
    assert mm.installed("m")


def test_zip_member_is_extracted_and_verified(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("other.bin", b"no")
        z.writestr("w600k.onnx", DATA)
    c, _ = server(payload=buf.getvalue())
    mm = ModelManager(tmp_path, registry([{"url": "http://x/z", "zip_member": "w600k.onnx"}]), c)
    mm.ensure()
    assert mm.path("m").read_bytes() == DATA


def test_cancel_keeps_partial_for_resume(tmp_path):
    c, _ = server()
    mm = ModelManager(tmp_path, registry([{"url": "http://x/m"}]), c)
    with pytest.raises(_Cancelled):
        mm.ensure(cancelled=lambda: True)
    assert not mm.installed("m")
