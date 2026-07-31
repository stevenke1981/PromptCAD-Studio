from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.models.cad import CadDocument
from app.models.dxf import DxfAnalysisResponse
from app.models.image import ImageAnalysisResponse
from app.services.job_service import JobService

BACKEND_CHOICES = [
    "auto",
    "cadquery",
    "build123d",
    "freecad",
    "openscad",
    "fusion360",
    "solidworks",
    "source_only",
]


def _generate(args) -> int:
    service = JobService(get_settings())
    manifest = asyncio.run(
        service.generate(
            args.prompt,
            args.planner,
            args.formats,
            not args.no_render,
            args.backend,
        )
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
    manifest = asyncio.run(
        service.generate_from_spec(
            spec,
            args.formats,
            not args.no_render,
            args.backend,
        )
    )
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if manifest.status != "failed" else 1


def _image(args) -> int:
    service = JobService(get_settings())
    image_path = Path(args.image)
    if image_path.stat().st_size > service.settings.max_image_bytes:
        print(
            f"Image exceeds the {service.settings.max_image_bytes} byte limit",
            file=sys.stderr,
        )
        return 2
    with image_path.open("rb") as handle:
        data = handle.read(service.settings.max_image_bytes + 1)
    analysis = asyncio.run(
        service.analyze_image(
            data,
            known_length_mm=args.known_length,
            thickness_mm=args.thickness,
        )
    )
    if args.analysis_output:
        Path(args.analysis_output).write_text(
            json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not args.confirm:
        print(json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0 if analysis.convertible else 2
    if not analysis.convertible:
        print(json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 2
    feature_tree = analysis.feature_tree
    if args.feature_tree_input:
        edited = ImageAnalysisResponse.model_validate_json(
            Path(args.feature_tree_input).read_text(encoding="utf-8")
        )
        if edited.image_sha256 != analysis.image_sha256:
            print("Feature Tree input does not match the supplied image.", file=sys.stderr)
            return 2
        feature_tree = edited.feature_tree
    manifest = asyncio.run(
        service.generate_from_image_feature_tree(
            analysis,
            feature_tree,
            formats=args.formats,
            render=not args.no_render,
            backend=args.backend,
        )
    )
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if manifest.status != "failed" else 1


def _read_dxf(path: Path, max_bytes: int) -> bytes | None:
    """Read a bounded DXF after checking its current on-disk size."""
    try:
        if path.stat().st_size > max_bytes:
            print(f"DXF 超過 {max_bytes} 位元組限制。", file=sys.stderr)
            return None
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        print(f"無法讀取 DXF：{exc}", file=sys.stderr)
        return None
    if len(data) > max_bytes:
        print(f"DXF 超過 {max_bytes} 位元組限制。", file=sys.stderr)
        return None
    return data


def _print_json(value: object) -> None:
    """Emit UTF-8 JSON independently of a legacy Windows console code page."""
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    raw_stdout = getattr(sys.stdout, "buffer", None)
    if raw_stdout is None:
        print(payload)
        return
    raw_stdout.write(f"{payload}\n".encode())


def _dxf(args) -> int:
    service = JobService(get_settings())
    path = Path(args.dxf)
    data = _read_dxf(path, service.settings.max_dxf_bytes)
    if data is None:
        return 2

    try:
        analysis = asyncio.run(
            service.analyze_dxf_bytes(
                data,
                thickness_mm=args.thickness,
                unit_override=args.units,
            )
        )
    except (ValueError, RuntimeError) as exc:
        print(f"DXF 分析失敗：{exc}", file=sys.stderr)
        return 2
    if args.analysis_output:
        try:
            Path(args.analysis_output).write_text(
                json.dumps(
                    analysis.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"無法寫入 DXF 分析結果：{exc}", file=sys.stderr)
            return 2
    if not args.confirm:
        _print_json(analysis.model_dump(mode="json"))
        return 0 if analysis.convertible else 2

    # Confirmation is intentionally bound to a fresh read and analysis, so a
    # 已審閱的特徵樹不能套用到之後變更過的 DXF。
    current_data = _read_dxf(path, service.settings.max_dxf_bytes)
    if current_data is None:
        return 2
    try:
        current_analysis = asyncio.run(
            service.analyze_dxf_bytes(
                current_data,
                thickness_mm=args.thickness,
                unit_override=args.units,
            )
        )
    except (ValueError, RuntimeError) as exc:
        print(f"DXF 重新分析失敗：{exc}", file=sys.stderr)
        return 2
    if not current_analysis.convertible:
        _print_json(current_analysis.model_dump(mode="json"))
        return 2

    feature_tree = current_analysis.feature_tree
    if args.feature_tree_input:
        try:
            edited = DxfAnalysisResponse.model_validate_json(
                Path(args.feature_tree_input).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            print(f"無法讀取特徵樹 JSON：{exc}", file=sys.stderr)
            return 2
        if edited.provenance.dxf_sha256 != current_analysis.provenance.dxf_sha256:
            print("特徵樹 JSON 的 DXF 雜湊與目前檔案不一致。", file=sys.stderr)
            return 2
        feature_tree = edited.feature_tree

    try:
        manifest = asyncio.run(
            service.generate_from_dxf_feature_tree(
                current_analysis,
                feature_tree,
                formats=args.formats,
                render=not args.no_render,
                backend=args.backend,
            )
        )
    except ValueError as exc:
        print(f"特徵樹驗證失敗：{exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"CAD 輸出失敗：{exc}", file=sys.stderr)
        return 1
    _print_json(manifest.model_dump(mode="json"))
    return 0 if manifest.status != "failed" else 1


def _doctor(_args) -> int:
    import importlib.util
    import shutil

    from app.services.backends import default_backend_registry

    settings = get_settings()
    cadquery = importlib.util.find_spec("cadquery") is not None
    openscad = shutil.which("openscad")
    capabilities = {
        item.backend_id: item
        for item in default_backend_registry().capabilities()
    }
    cadquery = cadquery and capabilities["cadquery"].runtime_available
    print("PromptCAD Studio doctor")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Data dir: {settings.data_dir.resolve()}")
    print(f"Planner mode: {settings.planner_mode}")
    print(f"LLM configured: {settings.llm_is_configured}")
    print(f"CadQuery: {'available' if cadquery else 'not installed'}")
    print(
        "Build123d: "
        + (
            "available"
            if capabilities["build123d"].runtime_available
            else "not installed / isolated extra"
        )
    )
    print(f"OpenSCAD: {openscad or 'not installed'}")
    if not cadquery and not openscad:
        print("Result: source-only mode. Use Docker/conda for STEP/STL/DXF.")
        return 1
    print("Result: CAD renderer available.")
    return 0


def _capabilities(_args) -> int:
    service = JobService(get_settings())
    try:
        value = {
            "contract_version": "1.0",
            "backends": [
                item.model_dump(mode="json")
                for item in service.backends.capabilities()
            ],
            "planners": [
                item.model_dump(mode="json")
                for item in service.planners.capabilities()
            ],
        }
        _print_json(value)
        return 0
    finally:
        service.close()


def _add_backend_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        default="auto",
        help="CAD backend；桌面 CAD adapter 僅輸出受控來源腳本",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promptcad", description="PromptCAD Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate CAD artifacts from a prompt")
    generate.add_argument("prompt")
    generate.add_argument("--planner", choices=["auto", "agent", "rule", "llm"], default="auto")
    generate.add_argument(
        "--formats",
        nargs="+",
        default=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
        choices=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
    )
    generate.add_argument("--no-render", action="store_true")
    _add_backend_argument(generate)
    generate.set_defaults(func=_generate)

    validate = sub.add_parser("validate", help="Validate a spec.json")
    validate.add_argument("spec")
    validate.set_defaults(func=_validate)

    render = sub.add_parser("render", help="Render an edited spec.json")
    render.add_argument("spec")
    render.add_argument(
        "--formats",
        nargs="+",
        default=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
        choices=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
    )
    render.add_argument("--no-render", action="store_true")
    _add_backend_argument(render)
    render.set_defaults(func=_render)

    image = sub.add_parser(
        "image",
        help="Extract a calibrated top-view image into an editable feature tree",
    )
    image.add_argument("image")
    image.add_argument(
        "--known-length",
        type=float,
        required=True,
        help="Known real length of the detected outer profile's longest edge in mm",
    )
    image.add_argument("--thickness", type=float, required=True, help="Part thickness in mm")
    image.add_argument(
        "--formats",
        nargs="+",
        default=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
        choices=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
    )
    image.add_argument(
        "--confirm",
        action="store_true",
        help="Acknowledge review and generate CAD artifacts",
    )
    image.add_argument(
        "--analysis-output",
        help="Write the calibrated analysis and editable Feature Tree to a JSON file",
    )
    image.add_argument(
        "--feature-tree-input",
        help="Use an edited analysis JSON previously written by --analysis-output",
    )
    image.add_argument("--no-render", action="store_true")
    _add_backend_argument(image)
    image.set_defaults(func=_image)

    dxf = sub.add_parser("dxf", help="分析 DXF 並以可編輯特徵樹產生 CAD")
    dxf.add_argument("dxf", help="DXF 檔案路徑")
    dxf.add_argument("--thickness", type=float, required=True, help="零件厚度（mm）")
    dxf.add_argument(
        "--units",
        choices=["auto", "mm", "inch", "cm"],
        default="auto",
        help="來源單位；auto 使用 DXF INSUNITS",
    )
    dxf.add_argument(
        "--analysis-output",
        help="將分析結果與可編輯特徵樹寫入 JSON 檔",
    )
    dxf.add_argument(
        "--feature-tree-input",
        help="使用 --analysis-output 寫出的已編輯 JSON；需要 --confirm",
    )
    dxf.add_argument("--confirm", action="store_true", help="確認特徵樹並輸出 CAD")
    dxf.add_argument(
        "--formats",
        nargs="+",
        default=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
        choices=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
    )
    dxf.add_argument("--no-render", action="store_true")
    _add_backend_argument(dxf)
    dxf.set_defaults(func=_dxf)

    doctor = sub.add_parser("doctor", help="Check local CAD runtime")
    doctor.set_defaults(func=_doctor)

    capabilities = sub.add_parser(
        "capabilities",
        help="Print machine-readable planner and CAD backend capabilities",
    )
    capabilities.set_defaults(func=_capabilities)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
