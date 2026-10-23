"""Write the release version into edic/__init__.py (the single source; pyproject reads it).
usage: set_version.py 1.2.3
"""
import re
import sys
from pathlib import Path

version = sys.argv[1].lstrip("v")
if not re.fullmatch(r"\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-]+)?", version):
    sys.exit(f"not a version: {version!r}")
p = Path(__file__).resolve().parent.parent / "edic" / "__init__.py"
s = p.read_text(encoding="utf-8")
new = re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', s)
if new == s and f'"{version}"' not in s:
    sys.exit("could not find __version__")
p.write_text(new, encoding="utf-8")
print("version ->", version)
