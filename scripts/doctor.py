from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util
import shutil

from app.core.config import get_settings


def main() -> int:
    settings = get_settings()
    cadquery = importlib.util.find_spec("cadquery") is not None
    openscad = shutil.which("openscad")
    print("PromptCAD Studio doctor")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Data dir: {settings.data_dir.resolve()}")
    print(f"Planner mode: {settings.planner_mode}")
    print(f"LLM configured: {settings.llm_is_configured}")
    print(f"CadQuery: {'available' if cadquery else 'not installed'}")
    print(f"OpenSCAD: {openscad or 'not installed'}")
    if not cadquery and not openscad:
        print("Result: source-only mode. Use Docker/conda for STEP/STL/DXF.")
        return 1
    print("Result: CAD renderer available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
