"""Builds dist/edm-ai.pyz - ONE file that runs the whole demo on any PC that has Python 3.10+:

    python edm-ai.pyz demo            (Windows:  py edm-ai.pyz demo)

On first start it unpacks itself to ~/.edm-ai/<version>/ (the web UI and demo data need real files)
and keeps the demo workspace there, so the .pyz can be copied around by USB / mail / file share.
"""
import hashlib
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from edm_ai import __version__  # noqa: E402

MAIN = '''import hashlib, os, runpy, sys, zipfile
from pathlib import Path

here = Path(__file__).resolve().parent if not zipfile.is_zipfile(sys.argv[0]) else Path(sys.argv[0]).resolve()
base = Path(os.environ.get("EDM_AI_HOME") or Path.home() / ".edm-ai") / "%(ver)s-%(tag)s"
if not (base / ".ready").exists():
    print("처음 실행: 압축을 풀고 있습니다 ->", base)
    with zipfile.ZipFile(here) as z:
        for n in z.namelist():
            if n != "__main__.py" and not n.endswith("/"):
                t = base / n
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(z.read(n))
    (base / ".ready").write_text("ok")
sys.path.insert(0, str(base))
os.chdir(base)
sys.argv[0] = "edm-ai"
runpy.run_module("edm_ai", run_name="__main__")
'''

files = []
for sub in ("edm_ai", "examples/demo"):
    for p in sorted((root / sub).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and "workspace" not in p.parts and "uploads" not in p.parts:
            files.append(p)
tag = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()[:8]
out = root / "dist" / "edm-ai.pyz"
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("__main__.py", MAIN % {"ver": __version__, "tag": tag})
    for p in files:
        z.write(p, p.relative_to(root).as_posix())
print(f"{out}  ({out.stat().st_size // 1024} KB, v{__version__}-{tag})")
