"""Write the sorted result to folders."""
from __future__ import annotations

import csv
import os
import re
import shutil
from pathlib import Path

from .review import Review

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def folder_name(template: str, number: int, name: str) -> str:
    label = template.format(number=number, name=name.strip()).strip(" .-_")
    return _BAD.sub("_", label) or f"{number:03d}"


def _place(src: Path, dst: Path, mode: str):
    if dst.exists():
        return
    if mode == "symlink":
        os.symlink(src, dst)
    elif mode == "hardlink":
        try:
            os.link(src, dst)
        except OSError:                       # different filesystem: fall back to a real copy
            shutil.copy2(src, dst)
    else:
        shutil.copy2(src, dst)


def plan(project, assign_mode: str | None = None, template: str | None = None, include_unsorted: bool = True):
    """What would be written: {folder: [(photo name, source path, how)]} - no files are touched."""
    review = Review(project)
    template = template or project.setting("folder_template")
    comps = {c["id"]: c for c in project.competitors()}
    photos = {p["id"]: p for p in project.photos()}
    folders: dict[str, list] = {}
    placed = set()
    for photo_id, comp_id, how in review.effective(assign_mode):
        c = comps[comp_id]
        folders.setdefault(folder_name(template, c["number"], c["name"]), []).append((photos[photo_id], how))
        placed.add(photo_id)
    if include_unsorted:
        rest = [p for pid, p in photos.items() if pid not in placed and p["processed"]]
        if rest:
            folders["_unsorted"] = [(p, "none") for p in rest]
    return folders


def export(project, dest: str | Path, mode: str | None = None, assign_mode: str | None = None,
           template: str | None = None, include_unsorted: bool = True, progress=None) -> dict:
    dest = Path(dest)
    mode = mode or project.setting("export_mode")
    folders = plan(project, assign_mode, template, include_unsorted)
    total = sum(len(v) for v in folders.values())
    done = 0
    rows = []
    for folder, items in sorted(folders.items()):
        out = dest / folder
        out.mkdir(parents=True, exist_ok=True)
        for photo, how in sorted(items, key=lambda x: (x[0]["taken"] or 0, x[0]["name"])):
            target = out / photo["name"]
            n = 1
            while target.exists() and target.resolve() != Path(photo["path"]).resolve() and not _same_file(target, Path(photo["path"])):
                target = out / f"{Path(photo['name']).stem}_{n}{Path(photo['name']).suffix}"      # same name from another card
                n += 1
            _place(Path(photo["path"]), target, mode)
            rows.append([folder, photo["name"], how, photo["path"]])
            done += 1
            if progress and done % 20 == 0:
                progress(done, total, photo["name"])
    with open(dest / "3edic-report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["folder", "photo", "decided_by", "source_path"])
        w.writerows(rows)
    if progress:
        progress(total, total, "")
    return {"dest": str(dest), "files": total, "folders": len(folders), "mode": mode}


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes()[:65536] == b.read_bytes()[:65536]
    except OSError:
        return False


def export_photos(project, photo_ids, dest: str | Path, mode: str = "copy", progress=None) -> dict:
    """Copy/link just these photos into one folder (the 'find a person' result)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ids = set(photo_ids)
    items = [p for p in project.photos() if p["id"] in ids]
    for i, photo in enumerate(items, 1):
        target = dest / photo["name"]
        n = 1
        while target.exists() and not _same_file(target, Path(photo["path"])):
            target = dest / f"{Path(photo['name']).stem}_{n}{Path(photo['name']).suffix}"
            n += 1
        _place(Path(photo["path"]), target, mode)
        if progress:
            progress(i, len(items), photo["name"])
    return {"dest": str(dest), "files": len(items), "mode": mode}
