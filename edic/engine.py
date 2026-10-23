"""The application core: settings, projects, models, jobs. The HTTP API is a thin layer over this."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path

from .jobs import Job, Jobs
from .models.manager import ModelManager
from .project import Project
from .vision import providers as vproviders

GLOBAL_DEFAULTS = {"language": "en", "use_arcface": False, "vlm_provider": None, "vlm_model": None}


def default_data_dir() -> Path:
    if os.environ.get("EDIC_DATA"):
        return Path(os.environ["EDIC_DATA"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "3EDIC"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "3EDIC"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "3edic"


class Engine:
    def __init__(self, data_dir: Path | None = None, models_dir: Path | None = None):
        self.data_dir = Path(data_dir or default_data_dir())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models = ModelManager(models_dir or self.data_dir / "models")
        self.jobs = Jobs()
        self._projects: dict[str, Project] = {}
        self._extractor = None
        self._extractor_key = None
        self._lock = threading.RLock()

    # --------------------------------------------------------------- settings
    def settings(self) -> dict:
        try:
            saved = json.loads((self.data_dir / "settings.json").read_text())
        except (OSError, ValueError):
            saved = {}
        return {**GLOBAL_DEFAULTS, **saved}

    def update_settings(self, changes: dict) -> dict:
        bad = set(changes) - set(GLOBAL_DEFAULTS)
        if bad:
            raise KeyError(", ".join(sorted(bad)))
        s = {**self.settings(), **changes}
        (self.data_dir / "settings.json").write_text(json.dumps(s, indent=1))
        return s

    def _secrets(self) -> dict:
        try:
            return json.loads((self.data_dir / "secrets.json").read_text())
        except (OSError, ValueError):
            return {}

    def set_api_key(self, provider: str, key: str | None):
        """Stored in a file only the current user can read. Removed when key is empty."""
        sec = self._secrets()
        if key:
            sec[provider] = key
        else:
            sec.pop(provider, None)
        path = self.data_dir / "secrets.json"
        path.write_text(json.dumps(sec))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def api_key(self, provider: str) -> str | None:
        return self._secrets().get(provider)

    def has_api_key(self, provider: str) -> bool:
        return bool(self.api_key(provider))

    # ---------------------------------------------------------------- projects
    def list_projects(self) -> list[dict]:
        out = []
        root = self.data_dir / "projects"
        for d in sorted(root.glob("*/project.db")) if root.exists() else []:
            try:
                out.append(self.project(d.parent.name).summary())
            except Exception:
                continue
        return sorted(out, key=lambda s: -(s.get("created") or 0))

    def project(self, pid: str) -> Project:
        with self._lock:
            if pid not in self._projects:
                root = self.data_dir / "projects" / pid
                if not (root / "project.db").exists():
                    raise KeyError(pid)
                self._projects[pid] = Project(root)
            return self._projects[pid]

    def create_project(self, name: str, photos_dir: str) -> Project:
        folder = Path(photos_dir).expanduser()
        if not folder.is_dir():
            raise ValueError("that folder does not exist")
        p = Project.create(self.data_dir, name.strip() or folder.name, folder)
        with self._lock:
            self._projects[p.id] = p
        return p

    def delete_project(self, pid: str):
        """Removes the project's database and thumbnails. The photos themselves are never touched."""
        with self._lock:
            p = self._projects.pop(pid, None) or self.project(pid)
            p.con.close()
        shutil.rmtree(self.data_dir / "projects" / pid, ignore_errors=True)

    # ------------------------------------------------------------------ vision
    def variant(self) -> str:
        return "arcface" if self.settings()["use_arcface"] and self.models.installed("arcface") else "basic"

    def extractor(self):
        """Built once per model set (heavy: loads the ONNX sessions)."""
        from .vision.bib import BibReader
        from .vision.extractor import Extractor
        from .vision.faces import FaceEngine
        from .vision.pose import PoseModel
        use_arc = self.variant() == "arcface"
        with self._lock:
            if self._extractor is None or self._extractor_key != use_arc:
                m = self.models
                self._extractor = Extractor(
                    PoseModel(m.path("pose")),
                    FaceEngine(m.path("yunet"), m.path("sface"), m.path("arcface") if use_arc else None),
                    BibReader())
                self._extractor_key = use_arc
            return self._extractor

    def hardware(self) -> dict:
        return vproviders.describe()

    # -------------------------------------------------------------------- jobs
    def install_models_job(self, ids=None, accept=()) -> Job:
        for i in accept:
            self.models.accept_license(i)

        def run(job: Job):
            return self.models.ensure(ids, progress=job.progress, cancelled=job.cancelled)
        return self.jobs.start("models", run, "Downloading models")

    def process_job(self, pid: str) -> Job:
        """Everything between 'photos in a folder' and 'suggestions ready': ingest, extract, (ArcFace), score."""
        project = self.project(pid)

        def run(job: Job):
            self.models.ensure(self._wanted_models(), progress=lambda d, t, m: job.progress(d, t, "Models · " + m),
                               cancelled=job.cancelled)
            job.progress(0, 0, "Looking for photos")
            added = project.ingest(progress=lambda d, t, m: job.progress(d, t, "Reading photo list"), stop=job.cancelled)
            ex = self.extractor()
            done = project.extract(ex, progress=lambda d, t, m: job.progress(d, t, m), stop=job.cancelled)
            if self.variant() == "arcface" and not job.cancelled():
                project.backfill_arc(ex.faces, progress=lambda d, t, m: job.progress(d, t, "ArcFace · " + m), stop=job.cancelled)
            job.progress(0, 0, "Scoring")
            res = project.rescore(self.variant())
            return {"added": added, "processed": done, **res}
        return self.jobs.start("process", run, project.get_meta("name"))

    def _wanted_models(self):
        ids = [m["id"] for m in self.models.registry["models"] if m["required"]]
        if self.settings()["use_arcface"]:
            ids.append("arcface")
        return ids
