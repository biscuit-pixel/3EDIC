"""The application core: settings, projects, models, jobs. The HTTP API is a thin layer over this."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from .jobs import Job, Jobs
from .models.manager import ModelManager
from .project import Project
from .updater import Updater
from .vision import providers as vproviders

GLOBAL_DEFAULTS = {"language": "en", "use_arcface": False, "vlm_provider": None, "vlm_model": None,
                   "check_updates": True, "update_dismissed": None,
                   "device": "auto",              # auto | cpu | gpu
                   "tutorial_seen": False, "context_frames": 4}


def platform_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "3EDIC"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "3EDIC"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "3edic"


def bootstrap_file() -> Path:
    """Always in the platform default place: it says where the working folder really is (Settings can move it)."""
    return platform_data_dir() / "location.json"


def default_data_dir() -> Path:
    if os.environ.get("EDIC_DATA"):
        return Path(os.environ["EDIC_DATA"])
    try:
        chosen = Path(json.loads(bootstrap_file().read_text())["data_dir"])
        if chosen.is_dir():
            return chosen
    except (OSError, ValueError, KeyError):
        pass
    return platform_data_dir()


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
        self.updater = Updater(self.data_dir)
        vproviders.set_preference(self.settings()["device"])

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
        if s["device"] not in ("auto", "cpu", "gpu"):
            raise KeyError("device")
        (self.data_dir / "settings.json").write_text(json.dumps(s, indent=1))
        if "device" in changes:                       # models are reopened on the new device the next time they are needed
            vproviders.set_preference(s["device"])
            with self._lock:
                self._extractor = None
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

    def create_project(self, name: str, photos_dir: str, roster_mode: str = "auto") -> Project:
        folder = Path(photos_dir).expanduser()
        if folder.is_file():                      # a file dropped on the window: its folder is meant
            folder = folder.parent
        if not folder.is_dir():
            raise ValueError("that folder does not exist")
        if roster_mode not in ("auto", "list"):
            raise ValueError("roster_mode must be auto or list")
        p = Project.create(self.data_dir, name.strip() or folder.name, folder)
        p.set_setting("roster_mode", roster_mode)
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
        key = (use_arc, vproviders.PREFERENCE)
        with self._lock:
            if self._extractor is None or self._extractor_key != key:
                m = self.models
                self._extractor = Extractor(
                    PoseModel(m.path("pose")),
                    FaceEngine(m.path("yunet"), m.path("sface"), m.path("arcface") if use_arc else None),
                    BibReader())
                self._extractor_key = key
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
            job.progress(0, 0, "Creating albums for the numbers found")
            albums = project.auto_roster()
            job.progress(0, 0, "Scoring")
            res = project.rescore(self.variant())
            return {"added": added, "processed": done, "albums": len(albums), **res}
        return self.jobs.start("process", run, project.get_meta("name"))

    def ingest_job(self, pid: str) -> Job:
        """Just list the photos (fast): the first step after choosing a folder."""
        project = self.project(pid)

        def run(job: Job):
            added = project.ingest(progress=lambda d, t, m: job.progress(d, t, m), stop=job.cancelled)
            return {"added": added}
        return self.jobs.start("ingest", run, "Reading the photo list")

    def prepare_job(self, pid: str) -> Job:
        """Find the people in a dozen photos so the user can show where the numbers are BEFORE the full analysis."""
        project = self.project(pid)

        def run(job: Job):
            self.models.ensure(self._wanted_models(), progress=lambda d, t, m: job.progress(d, t, "Models · " + m), cancelled=job.cancelled)
            if not project.photos():
                project.ingest(progress=lambda d, t, m: job.progress(d, t, "Reading the photo list"), stop=job.cancelled)
            job.progress(0, 0, "Loading the models")
            ex = self.extractor()
            n = project.prepare_samples(ex.pose, progress=lambda d, t, m: job.progress(d, t, "Looking at sample photos · " + m), stop=job.cancelled)
            return {"samples": n}
        return self.jobs.start("prepare", run, "Preparing sample photos")

    def status(self) -> dict:
        """Everything the bottom-left status bar shows: what is running now, what just finished, which device."""
        recent = [j for j in self.jobs.recent(5) if j["state"] != "running" and j.get("finished") and time.time() - j["finished"] < 20]
        d = vproviders.describe()
        return {"active": self.jobs.active(), "last": recent[0] if recent else None, "device": d["device"], "preference": d["preference"]}

    # ------------------------------------------------------- working folder
    def data_dir_info(self) -> dict:
        return {"current": str(self.data_dir), "default": str(platform_data_dir()), "custom": self.data_dir.resolve() != platform_data_dir().resolve(),
                "from_env": bool(os.environ.get("EDIC_DATA"))}

    def move_data_job(self, dest: str, move: bool = True) -> Job:
        """Point the app at another working folder (projects, models, settings) and optionally copy everything there."""
        target = Path(dest).expanduser().resolve()
        cur = self.data_dir.resolve()
        if target == cur:
            raise ValueError("that is already the working folder")
        if cur in target.parents or target in cur.parents:
            raise ValueError("choose a folder that is not inside the current one (or the other way round)")
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".edic-write-test"
        try:
            probe.write_text("ok")
            probe.unlink()
        except OSError:
            raise ValueError("3EDIČ cannot write to that folder")

        def run(job: Job):
            if move:
                files = [f for name in ("projects", "models") for f in (cur / name).rglob("*") if f.is_file()]
                files += [cur / n for n in ("settings.json", "secrets.json") if (cur / n).exists()]
                total = sum(f.stat().st_size for f in files) or 1
                done = 0
                for f in files:
                    out = target / f.relative_to(cur)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out)
                    done += f.stat().st_size
                    job.progress(done, total, f.name)
                    if job.cancelled():
                        raise RuntimeError("cancelled")
            if target == platform_data_dir().resolve():
                bootstrap_file().unlink(missing_ok=True)
            else:
                bootstrap_file().parent.mkdir(parents=True, exist_ok=True)
                bootstrap_file().write_text(json.dumps({"data_dir": str(target)}))
            return {"restart": True, "old": str(cur), "new": str(target)}
        return self.jobs.start("move-data", run, "Moving the working folder")

    def restart_app(self):
        """Start a fresh copy of this app and leave. The old working folder is never deleted."""
        import subprocess
        subprocess.Popen(list(sys.orig_argv), close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}))
        threading.Timer(0.8, lambda: os._exit(0)).start()

    def _wanted_models(self):
        ids = [m["id"] for m in self.models.registry["models"] if m["required"]]
        if self.settings()["use_arcface"]:
            ids.append("arcface")
        return ids
