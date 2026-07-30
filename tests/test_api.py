from __future__ import annotations


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_capabilities_advertise_rectangular_side_cutouts(client):
    response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert "rectangular_cutout" in response.json()["feature_types"]


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
