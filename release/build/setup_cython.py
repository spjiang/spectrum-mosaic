from __future__ import annotations

import os
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup

LIST = Path(os.environ.get("CYTHON_MODULES_FILE", "/build/release/build/cython_modules.txt"))
# 清单里的名字相对这个包；子包写成 geom/footprint
PACKAGE = os.environ.get("CYTHON_PACKAGE", "ms_mosaic")
MODULES = [
    line.strip()
    for line in LIST.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
]

exts: list[Extension] = []
missing: list[str] = []
for name in MODULES:
    src = Path(PACKAGE) / f"{name}.py"
    if not src.exists():
        missing.append(name)
        continue
    exts.append(Extension(f"{PACKAGE}.{name.replace('/', '.')}", [str(src)]))

if missing:
    print("skip missing modules:", ", ".join(missing))
if not exts:
    raise SystemExit("no modules to compile")

setup(
    name=f"{PACKAGE}_customer",
    ext_modules=cythonize(
        exts,
        language_level="3",
        compiler_directives={"boundscheck": False, "annotation_typing": False},
    ),
)
