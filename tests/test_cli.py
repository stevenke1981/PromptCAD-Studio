from __future__ import annotations

import json
from types import SimpleNamespace

from app import cli
from app.core.config import Settings


class _Analysis:
    def __init__(self, sha256: str, feature_tree: list[dict] | None = None) -> None:
        self.convertible = True
        self.provenance = SimpleNamespace(dxf_sha256=sha256)
        self.feature_tree = feature_tree or [{"id": "profile-01"}]

    def model_dump(self, **_kwargs):
        return {
            "convertible": self.convertible,
            "provenance": {"dxf_sha256": self.provenance.dxf_sha256},
            "feature_tree": self.feature_tree,
        }


class _Manifest:
    status = "source_only"

    def model_dump(self, **_kwargs):
        return {"status": self.status}


class _ImageAnalysis:
    convertible = True
    image_sha256 = "b" * 64
    feature_tree = [{"id": "profile-01"}]

    def model_dump(self, **_kwargs):
        return {
            "convertible": self.convertible,
            "image_sha256": self.image_sha256,
            "source_kind": "pdf",
            "source_page_index": 1,
            "feature_tree": self.feature_tree,
        }


class _ImageService:
    def __init__(self, max_image_bytes: int = 1024) -> None:
        self.settings = SimpleNamespace(max_image_bytes=max_image_bytes)
        self.analysis_calls = []
        self.error = None

    async def analyze_image(
        self,
        data,
        *,
        known_length_mm,
        thickness_mm,
        perspective_correction,
        page_index,
        content_profile,
        object_index,
        accept_line_art_holes,
    ):
        self.analysis_calls.append(
            (
                data,
                known_length_mm,
                thickness_mm,
                perspective_correction,
                page_index,
                content_profile,
                object_index,
                accept_line_art_holes,
            )
        )
        if self.error is not None:
            raise self.error
        return _ImageAnalysis()


class _DxfService:
    def __init__(self, max_dxf_bytes: int = 256) -> None:
        self.settings = SimpleNamespace(max_dxf_bytes=max_dxf_bytes)
        self.analysis_calls: list[tuple[bytes, float, str, str]] = []
        self.generation = None

    async def analyze_dxf_bytes(
        self,
        data,
        *,
        thickness_mm,
        unit_override,
        operation_mode,
    ):
        self.analysis_calls.append((data, thickness_mm, unit_override, operation_mode))
        return _Analysis("a" * 64)

    async def generate_from_dxf_feature_tree(
        self,
        analysis,
        feature_tree,
        *,
        formats,
        render,
        backend,
    ):
        self.generation = (analysis, feature_tree, formats, render, backend)
        return _Manifest()


def _args(*argv: str):
    return cli.build_parser().parse_args(["dxf", *argv])


def _image_args(*argv: str):
    return cli.build_parser().parse_args(["image", *argv])


def test_image_cli_forwards_pdf_page_and_perspective_options(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "drawing.pdf"
    source.write_bytes(b"%PDF-test")
    service = _ImageService()
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = _image_args(
        str(source),
        "--known-length",
        "120",
        "--thickness",
        "4",
        "--page",
        "1",
        "--perspective-correction",
    )

    assert args.func(args) == 0
    assert service.analysis_calls == [
        (b"%PDF-test", 120.0, 4.0, True, 1, "auto", None, False)
    ]
    assert json.loads(capsys.readouterr().out)["source_page_index"] == 1


def test_image_cli_forwards_content_profile_and_object_selection(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "patent.png"
    source.write_bytes(b"image-bytes")
    service = _ImageService()
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = _image_args(
        str(source),
        "--known-length",
        "80",
        "--thickness",
        "3",
        "--content-profile",
        "patent",
        "--object-index",
        "1",
        "--accept-line-art-holes",
    )

    assert args.func(args) == 0
    assert service.analysis_calls == [
        (b"image-bytes", 80.0, 3.0, False, 0, "patent", 1, True)
    ]


def test_image_cli_reports_analysis_error_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "invalid.pdf"
    source.write_bytes(b"%PDF-invalid")
    service = _ImageService()
    service.error = cli.ImageAnalysisError("PDF is invalid")
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = _image_args(
        str(source),
        "--known-length",
        "120",
        "--thickness",
        "4",
    )

    assert args.func(args) == 2
    error = capsys.readouterr().err
    assert error.strip() == "影像分析失敗：PDF is invalid"
    assert "Traceback" not in error


def test_dxf_cli_writes_editable_analysis_and_reanalyzes_before_confirmation(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "plate.dxf"
    source.write_bytes(b"minimal-dxf")
    output = tmp_path / "analysis.json"
    edited = tmp_path / "edited.json"
    edited.write_text("{}", encoding="utf-8")
    service = _DxfService()
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(
        cli.DxfAnalysisResponse,
        "model_validate_json",
        lambda _value: _Analysis("a" * 64, [{"id": "edited-profile"}]),
    )

    result = _args(
        str(source),
        "--thickness",
        "3",
        "--units",
        "cm",
        "--analysis-output",
        str(output),
        "--feature-tree-input",
        str(edited),
        "--confirm",
        "--formats",
        "json",
        "py",
        "--no-render",
    ).func(
        _args(
            str(source),
            "--thickness",
            "3",
            "--units",
            "cm",
            "--analysis-output",
            str(output),
            "--feature-tree-input",
            str(edited),
            "--confirm",
            "--formats",
            "json",
            "py",
            "--no-render",
        )
    )

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["provenance"]["dxf_sha256"] == "a" * 64
    assert len(service.analysis_calls) == 2
    assert service.analysis_calls[0] == (b"minimal-dxf", 3.0, "cm", "auto")
    assert service.generation[1] == [{"id": "edited-profile"}]
    assert service.generation[2:] == (["json", "py"], False, "auto")


def test_dxf_cli_rejects_oversized_file_before_analysis(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "oversized.dxf"
    source.write_bytes(b"too-large")
    service = _DxfService(max_dxf_bytes=2)
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())

    result = _args(str(source), "--thickness", "2").func(
        _args(str(source), "--thickness", "2")
    )

    assert result == 2
    assert not service.analysis_calls
    assert "超過" in capsys.readouterr().err


def test_dxf_cli_reports_analysis_errors_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "invalid.dxf"
    source.write_bytes(b"not-a-dxf")
    service = _DxfService()

    async def reject(*_args, **_kwargs):
        raise ValueError("DXF is invalid")

    service.analyze_dxf_bytes = reject
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())

    args = _args(str(source), "--thickness", "2")
    result = args.func(args)
    error = capsys.readouterr().err

    assert result == 2
    assert error.strip() == "DXF 分析失敗：DXF is invalid"
    assert "Traceback" not in error


def test_dxf_cli_reports_analysis_output_write_errors_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "plate.dxf"
    source.write_bytes(b"minimal-dxf")
    service = _DxfService()
    monkeypatch.setattr(cli, "JobService", lambda _settings: service)
    monkeypatch.setattr(cli, "get_settings", lambda: object())

    args = _args(
        str(source),
        "--thickness",
        "2",
        "--analysis-output",
        str(tmp_path),
    )
    result = args.func(args)
    error = capsys.readouterr().err

    assert result == 2
    assert error.startswith("無法寫入 DXF 分析結果：")
    assert "Traceback" not in error


def test_async_cli_enqueue_status_list_and_cancel(tmp_path, monkeypatch, capsys) -> None:
    settings = Settings(
        env="test",
        data_dir=tmp_path / "generated",
        planner_mode="rule",
        render_backend="source_only",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    enqueue = cli.build_parser().parse_args(
        [
            "async-generate",
            "畫一個長80mm、寬40mm、厚5mm的固定板",
            "--planner",
            "rule",
            "--formats",
            "json",
            "py",
            "--no-render",
        ]
    )
    assert enqueue.func(enqueue) == 0
    queued = json.loads(capsys.readouterr().out)
    assert queued["status"] == "queued"

    status = cli.build_parser().parse_args(
        ["queue-status", queued["queue_job_id"]]
    )
    assert status.func(status) == 0
    assert json.loads(capsys.readouterr().out)["queue_job_id"] == queued["queue_job_id"]

    listing = cli.build_parser().parse_args(["queue-list", "--limit", "1"])
    assert listing.func(listing) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1

    cancel = cli.build_parser().parse_args(
        ["queue-cancel", queued["queue_job_id"]]
    )
    assert cancel.func(cancel) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"
