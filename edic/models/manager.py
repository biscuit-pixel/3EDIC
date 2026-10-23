"""Model download manager: one call fetches everything the app needs.

- resumable (HTTP Range into a .part file), retried with back-off, several mirrors per model
- every file is checked against the SHA-256 in the registry before it is used
- models with a license gate are only downloaded after the user explicitly accepted it
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import zipfile
from importlib import resources
from pathlib import Path

import httpx

CHUNK = 1 << 20


class ModelError(RuntimeError):
    pass


def load_registry() -> dict:
    return json.loads((resources.files("edic.models") / "registry.json").read_text())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


class ModelManager:
    def __init__(self, models_dir: Path, registry: dict | None = None, client: httpx.Client | None = None):
        self.dir = Path(models_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry or load_registry()
        self.by_id = {m["id"]: m for m in self.registry["models"]}
        self.client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=60))
        self.accepted_path = self.dir / "accepted_licenses.json"
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- state
    def path(self, model_id: str) -> Path:
        return self.dir / self.by_id[model_id]["file"]

    def installed(self, model_id: str) -> bool:
        m = self.by_id[model_id]
        p = self.path(model_id)
        return p.exists() and p.stat().st_size == m["size"]      # full hash check happens once, at download

    def accepted(self) -> set[str]:
        try:
            return set(json.loads(self.accepted_path.read_text()))
        except (OSError, ValueError):
            return set()

    def accept_license(self, model_id: str):
        self.accepted_path.write_text(json.dumps(sorted(self.accepted() | {model_id})))

    def status(self) -> list[dict]:
        out = []
        for m in self.registry["models"]:
            part = self.path(m["id"]).with_suffix(".part")
            out.append({"id": m["id"], "title": m["title"], "purpose": m["purpose"], "required": m["required"],
                        "size": m["size"], "license": m["license"], "license_gate": m.get("license_gate"),
                        "installed": self.installed(m["id"]), "accepted": m["id"] in self.accepted() or not m.get("license_gate"),
                        "partial_bytes": part.stat().st_size if part.exists() else 0})
        return out

    def missing_required(self) -> list[str]:
        return [m["id"] for m in self.registry["models"] if m["required"] and not self.installed(m["id"])]

    def ready(self) -> bool:
        return not self.missing_required()

    # -------------------------------------------------------------- download
    def ensure(self, ids: list[str] | None = None, progress=None, cancelled=lambda: False) -> list[str]:
        """Download every listed (default: all required) model that is not installed. -> ids fetched.
        progress(done_bytes, total_bytes, message) covers all models together."""
        ids = ids if ids is not None else [m["id"] for m in self.registry["models"] if m["required"]]
        todo = [i for i in ids if not self.installed(i)]
        for i in todo:
            gate = self.by_id[i].get("license_gate")
            if gate and i not in self.accepted():
                raise ModelError(f"{self.by_id[i]['title']}: license not accepted yet")
        total = sum(self.by_id[i]["size"] for i in todo)
        base = 0
        fetched = []
        for i in todo:
            m = self.by_id[i]

            def on_bytes(done, _m=m, _base=base):
                if progress:
                    progress(_base + done, total, _m["title"])

            self._fetch_one(m, on_bytes, cancelled)
            base += m["size"]
            fetched.append(i)
        if progress:
            progress(total, total, "")
        return fetched

    def _fetch_one(self, m: dict, on_bytes, cancelled):
        errors = []
        for src in m["sources"]:
            for attempt in range(4):
                try:
                    self._download(m, src, on_bytes, cancelled)
                    return
                except _Cancelled:
                    raise
                except _BadHash as e:
                    errors.append(str(e))
                    break                                   # a corrupt mirror will not fix itself: next one
                except (httpx.HTTPError, OSError) as e:
                    errors.append(f"{src['url']}: {type(e).__name__}: {e}")
                    time.sleep(min(2 ** attempt, 8))        # back off, then resume from the .part file
        raise ModelError(f"could not download {m['title']}: " + " | ".join(errors[-3:]))

    def _download(self, m: dict, src: dict, on_bytes, cancelled):
        final = self.path(m["id"])
        member = src.get("zip_member")
        part = final.with_suffix(".part" if not member else ".zip.part")
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with self.client.stream("GET", src["url"], headers=headers) as r:
            if r.status_code == 416:                       # asked past the end: the part file is complete
                have = part.stat().st_size
            elif r.status_code in (200, 206):
                if r.status_code == 200 and have:          # server ignored Range: start over
                    have = 0
                    part.unlink()
                with open(part, "ab") as f:
                    for chunk in r.iter_bytes(CHUNK):
                        if cancelled():
                            raise _Cancelled()
                        f.write(chunk)
                        have += len(chunk)
                        on_bytes(min(have, m["size"]))
            else:
                r.raise_for_status()
        if member:
            tmp = final.with_suffix(".extract")
            with zipfile.ZipFile(part) as z, z.open(member) as zi, open(tmp, "wb") as out:
                shutil.copyfileobj(zi, out)
            part.unlink()
            part = tmp
        if sha256_file(part) != m["sha256"]:
            part.unlink(missing_ok=True)
            raise _BadHash(f"{m['title']}: checksum mismatch from {src['url']}")
        os.replace(part, final)


class _Cancelled(Exception):
    pass


class _BadHash(Exception):
    pass
