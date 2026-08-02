from __future__ import annotations

import json

import pytest

from app import cli
from app.models.cad import CadDocument
from app.models.manufacturing import ManufacturingDrawingSpec
from app.services.manufacturing import ManufacturingDrawingService


class _Manifest:
    status = "source_only"

    def model_dump(self, **_kwargs):
        return {"status": self.status}


class _Queued:
    def model_dump(self, **_kwargs):
        return {"queue_job_id": "queue-01", "status": "queued"}


def _cad_document() -> CadDocument:
    return CadDocument.model_validate(
        {
            "schema_version": "1.0",
            "name": "two-hole-plate",
            "source_prompt": "120 x 60 x 8 mm aluminum plate with two holes",
            "material": "aluminum",
            "base": {
                "kind": "plate",
                "length": 120,
                "width": 60,
                "thickness": 8,
            },
            "holes": [
                {"kind": "hole", "x": -20, "y": 0, "diameter": 6.6},
                {"kind": "hole", "x": 20, "y": 0, "diameter": 6.6},
            ],
            "planner": {
                "planner": "test",
                "confidence": 1,
                "review_required": False,
            },
        }
    )


def _write_inputs(tmp_path):
    document = _cad_document()
    drawing_spec = ManufacturingDrawingService().create_default(
        document,
        part_number="PC-CLI-001",
        drawing_number="DWG-CLI-001",
        author="CLI Test",
    )
    spec_path = tmp_path / "spec.json"
    drawing_path = tmp_path / "drawing-spec.json"
    spec_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    drawing_path.write_text(drawing_spec.model_dump_json(indent=2), encoding="utf-8")
    return document, drawing_spec, spec_path, drawing_path


def test_manufacturing_template_writes_requested_metadata(
    tmp_path, monkeypatch
) -> None:
    document, drawing_spec, spec_path, _ = _write_inputs(tmp_path)
    output = tmp_path / "template.json"
    calls = []

    class Service:
        def manufacturing_template(self, spec, **metadata):
            calls.append((spec, metadata))
            return drawing_spec

    monkeypatch.setattr(cli, "JobService", lambda _settings: Service())
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = cli.build_parser().parse_args(
        [
            "manufacturing-template",
            str(spec_path),
            "--output",
            str(output),
            "--part-number",
            "PC-CLI-001",
            "--drawing-number",
            "DWG-CLI-001",
            "--author",
            "CLI Test",
        ]
    )

    assert args.func(args) == 0
    assert calls == [
        (
            document,
            {
                "part_number": "PC-CLI-001",
                "drawing_number": "DWG-CLI-001",
                "author": "CLI Test",
            },
        )
    ]
    written = ManufacturingDrawingSpec.model_validate_json(
        output.read_text(encoding="utf-8")
    )
    assert written == drawing_spec


@pytest.mark.parametrize(
    ("action", "expected_version", "reviewer", "note"),
    [
        ("submit", 0, "designer", "ready for review"),
        ("approve", 1, "approver", "dimensions checked"),
        ("reject", 1, "approver", "revise tolerance"),
    ],
)
def test_manufacturing_review_builds_exact_transition_payload(
    action,
    expected_version,
    reviewer,
    note,
    monkeypatch,
    capsys,
) -> None:
    calls = []

    class Review:
        def model_dump(self, **_kwargs):
            return {"status": "recorded", "action": action}

    class Service:
        def transition_manufacturing_review(self, job_id, request):
            calls.append((job_id, request))
            return Review()

    monkeypatch.setattr(cli, "JobService", lambda _settings: Service())
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = cli.build_parser().parse_args(
        [
            "manufacturing-review",
            "01234567-89ab-cdef-0123-456789abcdef",
            action,
            "--expected-version",
            str(expected_version),
            "--reviewer",
            reviewer,
            "--note",
            note,
        ]
    )

    assert args.func(args) == 0
    assert len(calls) == 1
    job_id, request = calls[0]
    assert job_id == "01234567-89ab-cdef-0123-456789abcdef"
    assert request.model_dump(mode="json") == {
        "action": action,
        "expected_version": expected_version,
        "reviewer": reviewer,
        "note": note,
        "record_id": None,
        "occurred_at": None,
    }
    assert json.loads(capsys.readouterr().out)["action"] == action


def test_render_passes_drawing_spec_to_job_service(tmp_path, monkeypatch, capsys) -> None:
    document, drawing_spec, spec_path, drawing_path = _write_inputs(tmp_path)
    calls = []

    class Service:
        async def generate_from_spec(
            self,
            spec,
            formats,
            render,
            backend,
            *,
            drawing_spec=None,
        ):
            calls.append((spec, formats, render, backend, drawing_spec))
            return _Manifest()

    monkeypatch.setattr(cli, "JobService", lambda _settings: Service())
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = cli.build_parser().parse_args(
        [
            "render",
            str(spec_path),
            "--drawing-spec",
            str(drawing_path),
            "--formats",
            "json",
            "pdf",
            "--no-render",
            "--backend",
            "source_only",
        ]
    )

    assert args.func(args) == 0
    assert calls == [
        (document, ["json", "pdf"], False, "source_only", drawing_spec)
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "source_only"


def test_async_render_preserves_complete_drawing_spec_payload(
    tmp_path, monkeypatch, capsys
) -> None:
    document, drawing_spec, spec_path, drawing_path = _write_inputs(tmp_path)
    enqueued = []

    class Queue:
        def __init__(self, _settings):
            pass

        def enqueue(self, kind, payload):
            enqueued.append((kind, payload))
            return _Queued()

    monkeypatch.setattr(cli, "AsyncJobQueue", Queue)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    args = cli.build_parser().parse_args(
        [
            "async-render",
            str(spec_path),
            "--drawing-spec",
            str(drawing_path),
            "--formats",
            "json",
            "pdf",
            "--no-render",
            "--backend",
            "source_only",
        ]
    )

    assert args.func(args) == 0
    assert len(enqueued) == 1
    kind, payload = enqueued[0]
    assert kind == "spec"
    assert payload == {
        "spec": document.model_dump(mode="json"),
        "formats": ["json", "pdf"],
        "render": False,
        "backend": "source_only",
        "drawing_spec": drawing_spec.model_dump(mode="json"),
    }
    assert json.loads(capsys.readouterr().out) == {
        "queue_job_id": "queue-01",
        "status": "queued",
    }
