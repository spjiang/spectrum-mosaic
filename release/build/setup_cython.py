from __future__ import annotations

import os
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup

LIST = Path(os.environ.get("CYTHON_MODULES_FILE", "/build/release/build/cython_modules.txt"))
MODULES = [
    line.strip()
    for line in LIST.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
]

exts: list[Extension] = []
missing: list[str] = []
for name in MODULES:
    src = Path("ms_mosaic") / f"{name}.py"
    if not src.exists():
        missing.append(name)
        continue
    exts.append(Extension(f"ms_mosaic.{name}", [str(src)]))

if missing:
    print("skip missing modules:", ", ".join(missing))
if not exts:
    raise SystemExit("no modules to compile")

setup(
    name="ms_mosaic_customer",
    ext_modules=cythonize(exts, language_level="3", compiler_directives={"boundscheck": False}),
)
