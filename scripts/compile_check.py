from __future__ import annotations

import compileall
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ok = compileall.compile_dir(str(root / "app"), quiet=1)
ok = compileall.compile_file(str(root / "main.py"), quiet=1) and ok
print("COMPILE_OK" if ok else "COMPILE_FAILED")
raise SystemExit(0 if ok else 1)
