"""Live test of the download manager: real downloads, an interrupted + resumed one, and a corrupt-file case."""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from edic.models.manager import ModelError, ModelManager, _Cancelled  # noqa: E402

d = Path(tempfile.mkdtemp(prefix="edic_models_"))
mm = ModelManager(d)
print("ready before:", mm.ready(), "missing:", mm.missing_required())

# 1) interrupt the big download after ~20 MB, then resume
stop_at = 20 << 20
seen = []
def prog(done, total, msg): seen.append((done, total, msg))
try:
    mm.ensure(["pose"], progress=prog, cancelled=lambda: bool(seen) and seen[-1][0] >= stop_at)
except _Cancelled:
    pass
part = d / "yolov8m-pose.part"
print("interrupted: .part =", part.stat().st_size >> 20, "MB, installed:", mm.installed("pose"))
seen.clear()
mm.ensure(["pose"], progress=prog)
print("resumed ->", "installed:", mm.installed("pose"), "first progress report after resume started at >=", seen[0][0] >> 20, "MB")

# 2) all required models, one call
mm.ensure(progress=prog)
print("ready after:", mm.ready())

# 3) corrupt a file in place of a download: wrong bytes of the right size must still be caught
bad = ModelManager(Path(tempfile.mkdtemp(prefix="edic_bad_")))
bad.by_id["yunet"]["sha256"] = "0" * 64
try:
    bad.ensure(["yunet"])
except ModelError as e:
    print("bad checksum rejected:", str(e)[:90])
print("bad install left behind:", bad.installed("yunet"))

# 4) license gate
try:
    mm.ensure(["arcface"])
except ModelError as e:
    print("license gate:", e)
mm.accept_license("arcface")
mm.ensure(["arcface"], progress=prog)
print("arcface installed:", mm.installed("arcface"))
print([ (s["id"], s["installed"]) for s in mm.status()])
shutil.rmtree(d, ignore_errors=True)
