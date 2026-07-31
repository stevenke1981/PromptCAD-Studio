from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile
from pathlib import Path


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_capabilities_advertise_rectangular_side_cutouts(client):
    response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert "rectangular_cutout" in response.json()["feature_types"]
    assert "profile_extrusion" in response.json()["base_features"]
    assert response.json()["dxf_entities"] == [
        "LINE",
        "ARC",
        "CIRCLE",
        "LWPOLYLINE",
        "POLYLINE",
    ]


def test_dxf_analysis_review_gate_and_generation(client):
    source = Path("examples/dxf-to-cad/plate-line-arc-four-holes-mm.dxf").read_bytes()
    response = client.post(
        "/api/v1/dxf-analysis",
        files={"dxf": ("source-name-is-not-persisted.dxf", source, "image/vnd.dxf")},
        data={"thickness_mm": "6", "unit_override": "auto"},
    )
    assert response.status_code == 200, response.text
    analysis = response.json()
    assert analysis["convertible"] is True
    assert analysis["review_required"] is True
    assert analysis["analysis_token"]
    assert analysis["provenance"]["source_unit"] == "mm"
    assert [segment["kind"] for segment in analysis["outer_profile"]["segments"]] == [
        "line",
        "arc",
        "line",
        "arc",
    ]
    assert len(analysis["holes"]) == 4
    assert analysis["warnings"] == [
        "DXF 幾何已轉為可編輯 Feature Tree；輸出 CAD 前必須人工確認。"
    ]

    tampered = dict(analysis)
    tampered["provenance"] = {
        **analysis["provenance"],
        "byte_length": analysis["provenance"]["byte_length"] + 1,
    }
    rejected = client.post(
        "/api/v1/generate-from-dxf-feature-tree",
        json={
            "analysis": tampered,
            "feature_tree": analysis["feature_tree"],
            "formats": ["json", "py", "scad", "svg"],
            "render": False,
        },
    )
    assert rejected.status_code == 422
    assert "provenance" in rejected.text

    generated = client.post(
        "/api/v1/generate-from-dxf-feature-tree",
        json={
            "analysis": analysis,
            "feature_tree": analysis["feature_tree"],
            "formats": ["json", "py", "scad", "svg"],
            "render": False,
        },
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["planner_used"] == "dxf-feature-tree"
    assert body["spec"]["base"]["kind"] == "profile_extrusion"
    names = {item["filename"] for item in body["artifacts"]}
    assert {"dxf-analysis.json", "dxf-feature-tree.json"} <= names
    assert all(not name.lower().endswith(".dxf") for name in names)


def test_invalid_dxf_never_creates_a_job(client):
    before = client.get("/api/v1/jobs").json()
    response = client.post(
        "/api/v1/dxf-analysis",
        files={"dxf": ("archive.dxf", b"PK\x03\x04not-a-dxf", "image/vnd.dxf")},
        data={"thickness_mm": "6", "unit_override": "auto"},
    )
    assert response.status_code == 422
    assert client.get("/api/v1/jobs").json() == before


def test_dxf_worker_timeout_cleans_parent_owned_temporary_file(
    client,
    monkeypatch,
):
    service = client.app.state.jobs
    source = Path("examples/dxf-to-cad/plate-line-arc-four-holes-mm.dxf").read_bytes()
    real_named_temporary_file = tempfile.NamedTemporaryFile
    created: list[Path] = []
    worker_options: dict = {}

    def tracked_temporary_file(*args, **kwargs):
        stream = real_named_temporary_file(*args, **kwargs)
        created.append(Path(stream.name))
        return stream

    def time_out(*_args, **kwargs):
        worker_options.update(kwargs)
        raise subprocess.TimeoutExpired("dxf-worker", 1)

    monkeypatch.setattr("app.services.job_service.subprocess.run", time_out)
    monkeypatch.setattr(
        "app.services.job_service.tempfile.NamedTemporaryFile",
        tracked_temporary_file,
    )
    try:
        service._run_dxf_worker(source, 6, "auto")
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected DXF worker timeout")

    assert created
    assert all(not path.exists() for path in created)
    assert all(service.settings.data_dir not in path.parents for path in created)
    assert worker_options["shell"] is False
    assert worker_options["cwd"] == Path(__file__).resolve().parents[1]
    assert not any(key.startswith("PROMPTCAD_") for key in worker_options["env"])


def test_dxf_multipart_body_is_rejected_before_parser(client, monkeypatch):
    called = False

    async def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not run")

    monkeypatch.setattr(client.app.state.jobs, "analyze_dxf_upload", should_not_run)
    response = client.post(
        "/api/v1/dxf-analysis",
        files={"dxf": ("oversized.dxf", b"0" * 5_200_000, "image/vnd.dxf")},
        data={"thickness_mm": "6", "unit_override": "auto"},
    )
    assert response.status_code == 413
    assert called is False


def test_feature_tree_json_bodies_are_limited_before_schema_parsing(client):
    oversized = b'{"payload":"' + b"x" * 1_050_000 + b'"}'
    for endpoint in (
        "/api/v1/image-feature-tree-to-spec",
        "/api/v1/generate-from-image-feature-tree",
        "/api/v1/dxf-feature-tree-to-spec",
        "/api/v1/generate-from-dxf-feature-tree",
    ):
        response = client.post(
            endpoint,
            content=oversized,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413, endpoint


def test_generate_source_only(client):
    response = client.post(
        "/api/v1/generate",
        json={
            "prompt": "長120mm寬60mm厚10mm固定板，四角M6通孔，R5",
            "planner": "rule",
            "formats": ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
            "render": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "source_only"
    names = {item["filename"] for item in body["artifacts"]}
    assert {
        "spec.json",
        "validation.json",
        "model.py",
        "model.scad",
        "preview.svg",
        "drawing.pdf",
    } <= names
    assert body["spec"]["base"]["kind"] == "plate"

    job = client.get(f"/api/v1/jobs/{body['job_id']}")
    assert job.status_code == 200
    bundle = client.get(f"/api/v1/jobs/{body['job_id']}/bundle.zip")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    rogue = client.app.state.jobs.storage.path(body["job_id"]) / "raw-upload.dxf"
    rogue.write_text("unlisted source", encoding="utf-8")
    bundle = client.get(f"/api/v1/jobs/{body['job_id']}/bundle.zip")
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert "manifest.json" in archive.namelist()
        assert "raw-upload.dxf" not in archive.namelist()


def test_invalid_download_name_rejected(client):
    response = client.get("/api/v1/jobs/not-an-id/files/../../etc/passwd")
    assert response.status_code in {404, 422}


def test_generate_from_edited_spec(client):
    generated = client.post(
        "/api/v1/generate",
        json={
            "prompt": "長100mm寬50mm厚8mm固定板，兩個5mm孔",
            "planner": "rule",
            "formats": ["json", "py", "scad", "svg"],
            "render": False,
        },
    ).json()
    spec = generated["spec"]
    spec["base"]["length"] = 140

    response = client.post(
        "/api/v1/generate-from-spec",
        json={"spec": spec, "formats": ["json", "py", "scad", "svg"], "render": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["planner_used"] == "manual-dsl"
    assert body["spec"]["base"]["length"] == 140
    assert body["status"] == "source_only"


def test_invalid_geometry_is_saved_but_never_rendered(client):
    spec = {
        "schema_version": "1.0",
        "name": "unsafe-hole",
        "source_prompt": "manual test",
        "unit": "mm",
        "material": None,
        "base": {"kind": "plate", "length": 40, "width": 30, "thickness": 5},
        "holes": [
            {
                "kind": "hole",
                "x": 19,
                "y": 0,
                "z": 0,
                "axis": "z",
                "diameter": 6,
                "hole_type": "through",
                "depth": None,
                "thread": None,
                "counterbore_diameter": None,
                "counterbore_depth": None,
                "countersink_diameter": None,
                "countersink_angle": None,
            }
        ],
        "fillets": [],
        "chamfers": [],
        "assumptions": [],
        "notes": [],
        "planner": {"planner": "manual", "confidence": 1, "review_required": False},
    }
    response = client.post(
        "/api/v1/generate-from-spec",
        json={"spec": spec, "formats": ["step", "stl", "json", "py"], "render": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["renderer_used"] == "validation-blocked"
    assert body["validation"]["valid"] is False
    names = {item["filename"] for item in body["artifacts"]}
    assert "model.py" in names
    assert "model.step" not in names
    assert "model.stl" not in names


def test_api_token_protects_api_files_and_bundle(tmp_path):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        env="test",
        data_dir=tmp_path / "protected-generated",
        planner_mode="rule",
        render_backend="source_only",
        api_token="top-secret",
    )
    headers = {"X-API-Key": "top-secret"}
    with TestClient(create_app(settings)) as protected:
        assert protected.get("/api/v1/health").status_code == 401
        assert protected.get("/api/v1/health", headers=headers).status_code == 200
        response = protected.post(
            "/api/v1/generate",
            headers=headers,
            json={
                "prompt": "長80寬40厚6的板",
                "planner": "rule",
                "formats": ["json", "py", "svg"],
                "render": False,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        spec_artifact = next(item for item in body["artifacts"] if item["filename"] == "spec.json")
        assert protected.get(spec_artifact["url"]).status_code == 401
        assert protected.get(spec_artifact["url"], headers=headers).status_code == 200
        bundle_url = f"/api/v1/jobs/{body['job_id']}/bundle.zip"
        assert protected.get(bundle_url, headers=headers).status_code == 200
