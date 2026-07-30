from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from app.core.config import get_settings
from app.services.job_service import JobService


async def main():
    service = JobService(get_settings())
    manifest = await service.generate(
        "鋁合金固定板，長120mm、寬60mm、厚10mm，四角M6通孔，R5",
        "rule",
        ["step", "stl", "dxf", "svg", "py", "scad", "json"],
        True,
    )
    print(manifest.model_dump_json(indent=2))
    if manifest.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
