"""Write the sorted result to folders."""
from __future__ import annotations

import csv
import os
import re
import shutil
from pathlib import Path

from .review import Review

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def folder_name(template: str, number: int, name: str, variant: int = 1, kind: str = "competitor") -> str:
    if kind == "others":
        return "Others"
    label = template.format(number=number, name=name.strip()).strip(" .-_")
    label = _BAD.sub("_", label) or f"{number:03d}"
    return label + (f"_{variant}" if variant > 1 else "")


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


GROUPS = "_groups"
GROUP_MODES = ("all", "main", "separate", "both")


def _apply_group_mode(project, pairs, comps, mode):
    """A photo with several competitors: keep it in every album (all), only in the album of the biggest person (main),
    only in the Group photos folder (separate) or in both (both). -> (pairs, ids of the group photos that go to GROUPS)"""
    if mode not in GROUP_MODES or mode == "all":
        return pairs, set()
    by_photo: dict[int, list] = {}
    for pair in pairs:
        if comps[pair[1]]["kind"] == "competitor":
            by_photo.setdefault(pair[0], []).append(pair)
    groups = {pid: ps for pid, ps in by_photo.items() if len({p[1] for p in ps}) > 1}
    if not groups:
        return pairs, set()
    people = project.load_persons()
    with project.lock:
        who = {(r["photo_id"], r["competitor_id"]): r["person_idx"] for r in project.con.execute("SELECT photo_id,competitor_id,person_idx FROM assignments WHERE person_idx IS NOT NULL")}

    def area(photo_id, comp_id):
        i = who.get((photo_id, comp_id))
        ps = people.get(photo_id, [])
        return (ps[i].box[2] - ps[i].box[0]) * (ps[i].box[3] - ps[i].box[1]) if i is not None and 0 <= i < len(ps) else 0.0

    drop = set()
    for pid, ps in groups.items():
        if mode == "main":
            keep = max(ps, key=lambda p: area(pid, p[1]))
            drop |= {(pid, p[1]) for p in ps if p is not keep}
        elif mode == "separate":
            drop |= {(pid, p[1]) for p in ps}
    return [p for p in pairs if (p[0], p[1]) not in drop], (set(groups) if mode in ("separate", "both") else set())


def plan(project, assign_mode: str | None = None, template: str | None = None, include_unsorted: bool = True, group_mode: str | None = None):
    """What would be written: {folder: [(photo name, source path, how)]} - no files are touched."""
    review = Review(project)
    template = template or project.setting("folder_template")
    comps = {c["id"]: c for c in project.competitors(include_others=True)}
    photos = {p["id"]: p for p in project.photos()}
    folders: dict[str, list] = {}
    placed = set()
    pairs, group_ids = _apply_group_mode(project, review.effective(assign_mode), comps, group_mode or project.setting("group_mode"))
    for photo_id, comp_id, how in pairs:
        c = comps[comp_id]
        folders.setdefault(folder_name(template, c["number"], c["name"], c["variant"], c["kind"]), []).append((photos[photo_id], how))
        placed.add(photo_id)
    if group_ids:
        folders[GROUPS] = [(photos[i], "group") for i in sorted(group_ids)]
        placed |= group_ids
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
