from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.models.cad import CadDocument
from app.services.job_service import JobService


def _generate(args) -> int:
    service = JobService(get_settings())
    manifest = asyncio.run(
        service.generate(args.prompt, args.planner, args.formats, not args.no_render)
    )
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if manifest.status != "failed" else 1


def _validate(args) -> int:
    service = JobService(get_settings())
    spec = CadDocument.model_validate_json(Path(args.spec).read_text(encoding="utf-8"))
    report = service.validate(spec)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.valid else 2


def _render(args) -> int:
    service = JobService(get_settings())
    spec = CadDocument.model_validate_json(Path(args.spec).read_text(encoding="utf-8"))
    manifest = asyncio.run(service.generate_from_spec(spec, args.formats, not args.no_render))
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if manifest.status != "failed" else 1


def _doctor(_args) -> int:
    import importlib.util
    import shutil

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promptcad", description="PromptCAD Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate CAD artifacts from a prompt")
    generate.add_argument("prompt")
    generate.add_argument("--planner", choices=["auto", "rule", "llm"], default="auto")
    generate.add_argument(
        "--formats",
        nargs="+",
        default=["step", "stl", "dxf", "svg", "py", "scad", "json"],
        choices=["step", "stl", "dxf", "svg", "py", "scad", "json"],
    )
    generate.add_argument("--no-render", action="store_true")
    generate.set_defaults(func=_generate)

    validate = sub.add_parser("validate", help="Validate a spec.json")
    validate.add_argument("spec")
    validate.set_defaults(func=_validate)

    render = sub.add_parser("render", help="Render an edited spec.json")
    render.add_argument("spec")
    render.add_argument(
        "--formats",
        nargs="+",
        default=["step", "stl", "dxf", "svg", "py", "scad", "json"],
        choices=["step", "stl", "dxf", "svg", "py", "scad", "json"],
    )
    render.add_argument("--no-render", action="store_true")
    render.set_defaults(func=_render)

    doctor = sub.add_parser("doctor", help="Check local CAD runtime")
    doctor.set_defaults(func=_doctor)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
