r"""Repair the project's existing Windows venv config without deleting packages."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_CFG = ROOT / ".venv" / "pyvenv.cfg"
RUNTIME = Path(r"C:\Users\USUARIO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python")
PYTHON = RUNTIME / "python.exe"
BACKUP = ROOT / "reports" / "pyvenv.cfg.before-repair"


def main() -> int:
    if not PYTHON.is_file() or not VENV_CFG.is_file():
        raise SystemExit("Existing .venv config or replacement interpreter is missing.")
    version = subprocess.run([str(PYTHON), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                             check=True, capture_output=True, text=True).stdout.strip()
    if not version.startswith("3.12."):
        raise SystemExit(f"Expected Python 3.12, found {version}")
    text = VENV_CFG.read_text(encoding="utf-8")
    if f"home = {RUNTIME}" in text and f"version = {version}" in text:
        print(f"Already repaired: {VENV_CFG}")
        return 0
    if not BACKUP.exists():
        shutil.copy2(VENV_CFG, BACKUP)
    lines = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if key == "home":
            line = f"home = {RUNTIME}"
        elif key == "version":
            line = f"version = {version}"
        elif key == "executable":
            line = f"executable = {PYTHON}"
        elif key == "command":
            line = f"command = {PYTHON} -m venv {ROOT / '.venv'}"
        lines.append(line)
    VENV_CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Repaired existing environment; original config preserved at {BACKUP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
