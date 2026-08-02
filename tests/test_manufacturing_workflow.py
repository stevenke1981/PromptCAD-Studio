from __future__ import annotations

import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app
from app.worker import AsyncJobWorker


def _plate_spec() -> dict:
    return {
        "schema_version": "1.0",
        "name": "two-hole-mounting-plate",
        "source_prompt": "120 x 60 x 8 mm mounting plate with two M6 clearance holes",
        "unit": "mm",
        "material": "aluminum",
        "base": {"kind": "plate", "length": 120, "width": 60, "thickness": 8},
        "holes": [
            {"kind": "hole", "x": -20, "y": 0, "diameter": 6.6, "hole_type": "clearance"},
            {"kind": "hole", "x": 20, "y": 0, "diameter": 6.6, "hole_type": "clearance"},
        ],
        "cutouts": [],
        "fillets": [],
        "chamfers": [],
        "standards": [],
        "assumptions": [],
        "notes": [],
        "planner": {"planner": "manual", "confidence": 1, "review_required": False},
    }


def _create_manufacturing_job(client: TestClient) -> tuple[str, dict]:
    spec = _plate_spec()
    template = client.post(
        "/api/v1/manufacturing-template",
        json={
            "spec": spec,
            "part_number": "PC-MVP-001",
            "drawing_number": "DWG-PC-MVP-001",
            "author": "Manufacturing test",
        },
    )
    assert template.status_code == 200, template.text
    drawing_spec = template.json()
    assert drawing_spec["review_status"] == "draft"
    assert drawing_spec["review_version"] == 0

    generated = client.post(
        "/api/v1/generate-from-spec",
        json={
            "spec": spec,
            "drawing_spec": drawing_spec,
            "formats": ["json", "pdf"],
            "render": False,
            "backend": "source_only",
        },
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    names = {artifact["filename"] for artifact in body["artifacts"]}
    assert {"spec.json", "drawing-spec.json", "drawing.pdf"} <= names
    assert any(name.startswith("manufacturing-review-v000") for name in names)
    return body["job_id"], drawing_spec


def _transition(
    client: TestClient,
    job_id: str,
    *,
    action: str,
    expected_version: int,
    reviewer: str = "Ada Reviewer",
    note: str = "",
):
    return client.post(
        f"/api/v1/jobs/{job_id}/manufacturing-review/transitions",
        json={
            "action": action,
            "reviewer": reviewer,
            "note": note,
            "expected_version": expected_version,
        },
    )


def test_manufacturing_review_approval_is_additive_and_persists(settings):
    with TestClient(create_app(settings)) as client:
        job_id, _drawing_spec = _create_manufacturing_job(client)
        draft = client.get(f"/api/v1/jobs/{job_id}/manufacturing-review")
        assert draft.status_code == 200, draft.text
        initial = draft.json()
        assert initial["status"] == "draft"
        assert initial["version"] == 0
        assert initial["actor_assurance"] == "self_asserted"
        assert initial["events"] == []

        submitted = _transition(
            client,
            job_id,
            action="submit",
            expected_version=0,
            note="Ready for manufacturing review",
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "in_review"
        assert submitted.json()["version"] == 1

        approved = _transition(
            client,
            job_id,
            action="approve",
            expected_version=1,
            reviewer="Grace Approver",
            note="Dimensions and tolerances verified",
        )
        assert approved.status_code == 200, approved.text
        final = approved.json()
        assert final["status"] == "approved"
        assert final["version"] == 2
        assert len(final["events"]) == 2
        assert "self" in str(final["events"][-1]).lower() or final["events"][-1]["reviewer"] == "Grace Approver"

        terminal = _transition(
            client,
            job_id,
            action="submit",
            expected_version=2,
        )
        assert terminal.status_code == 409

        job_dir = settings.data_dir / job_id
        pdfs = sorted(job_dir.glob("*.pdf"))
        drawing_specs = sorted(job_dir.glob("drawing-spec*.json"))
        review_snapshots = sorted(job_dir.glob("manufacturing-review-v*.json"))
        assert len(pdfs) == 3
        assert len(drawing_specs) == 3
        assert len(review_snapshots) == 3
        assert len({path.name for path in pdfs}) == 3

        (job_dir / "rogue-secret.txt").write_text("must not be bundled", encoding="utf-8")
        bundle = client.get(f"/api/v1/jobs/{job_id}/bundle.zip")
        assert bundle.status_code == 200, bundle.text
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            bundled = set(archive.namelist())
        assert {path.name for path in pdfs} <= bundled
        assert {path.name for path in drawing_specs} <= bundled
        assert {path.name for path in review_snapshots} <= bundled
        assert "rogue-secret.txt" not in bundled
        assert not any(name.endswith(".claim") for name in bundled)

    with TestClient(create_app(settings)) as reloaded:
        persisted = reloaded.get(f"/api/v1/jobs/{job_id}/manufacturing-review")
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["status"] == "approved"
        assert persisted.json()["version"] == 2


def test_manufacturing_review_reject_requires_note_and_versions_fail_closed(client):
    job_id, _drawing_spec = _create_manufacturing_job(client)
    submitted = _transition(
        client,
        job_id,
        action="submit",
        expected_version=0,
    )
    assert submitted.status_code == 200, submitted.text

    stale = _transition(
        client,
        job_id,
        action="approve",
        expected_version=0,
    )
    assert stale.status_code == 409
    assert "version" in stale.text.lower()

    missing_comment = _transition(
        client,
        job_id,
        action="reject",
        expected_version=1,
        note="   ",
    )
    assert missing_comment.status_code == 422

    rejected = _transition(
        client,
        job_id,
        action="reject",
        expected_version=1,
        note="Hole tolerance must be tightened",
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["version"] == 2

    terminal = _transition(
        client,
        job_id,
        action="approve",
        expected_version=2,
    )
    assert terminal.status_code == 409


def test_manufacturing_review_detects_bound_file_tampering(client, settings):
    job_id, _drawing_spec = _create_manufacturing_job(client)
    job_dir = settings.data_dir / job_id
    spec_path = job_dir / "spec.json"
    original_digest = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    spec_path.write_bytes(spec_path.read_bytes() + b"\n")
    assert hashlib.sha256(spec_path.read_bytes()).hexdigest() != original_digest

    review = client.get(f"/api/v1/jobs/{job_id}/manufacturing-review")
    assert review.status_code == 409
    assert "integrity" in review.text.lower()

    transition = _transition(
        client,
        job_id,
        action="submit",
        expected_version=0,
    )
    assert transition.status_code == 409
    assert not list(job_dir.glob("drawing-review-v*.pdf"))


def test_manufacturing_review_serializes_competing_expected_versions(client, settings):
    job_id, _drawing_spec = _create_manufacturing_job(client)

    def submit(reviewer: str):
        return _transition(
            client,
            job_id,
            action="submit",
            expected_version=0,
            reviewer=reviewer,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, ["Reviewer One", "Reviewer Two"]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    current = client.get(f"/api/v1/jobs/{job_id}/manufacturing-review")
    assert current.status_code == 200, current.text
    assert current.json()["version"] == 1
    assert len(current.json()["events"]) == 1
    job_dir = settings.data_dir / job_id
    assert len(list(job_dir.glob("drawing-review-v*.pdf"))) == 1


def test_manufacturing_review_serializes_across_service_instances(settings):
    with (
        TestClient(create_app(settings)) as first,
        TestClient(create_app(settings)) as second,
    ):
        job_id, _drawing_spec = _create_manufacturing_job(first)

        def submit(entry):
            client, reviewer = entry
            return _transition(
                client,
                job_id,
                action="submit",
                expected_version=0,
                reviewer=reviewer,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    submit,
                    [(first, "Process One"), (second, "Process Two")],
                )
            )

        assert sorted(response.status_code for response in responses) == [200, 409]
        persisted = first.get(f"/api/v1/jobs/{job_id}/manufacturing-review")
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["version"] == 1
        assert len(persisted.json()["events"]) == 1


def test_manufacturing_review_recovers_expired_interrupted_claim(client, settings):
    job_id, _drawing_spec = _create_manufacturing_job(client)
    job_dir = settings.data_dir / job_id
    claim = job_dir / "manufacturing-review-v001.claim"
    orphan_pdf = job_dir / "drawing-review-v001-in_review-interrupted.pdf"
    orphan_spec = job_dir / "drawing-spec-review-v001-interrupted.json"
    claim.write_text(
        json.dumps(
            {
                "claim_id": "interrupted-process",
                "created_at": "2000-01-01T00:00:00+00:00",
                "lease_expires_at": 0,
            }
        ),
        encoding="utf-8",
    )
    orphan_pdf.write_bytes(b"partial pdf")
    orphan_spec.write_text("{}", encoding="utf-8")

    submitted = _transition(
        client,
        job_id,
        action="submit",
        expected_version=0,
        note="Recover interrupted transaction",
    )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["version"] == 1
    assert not claim.exists()
    assert not orphan_pdf.exists()
    assert not orphan_spec.exists()
    assert len(list(job_dir.glob("drawing-review-v001-*.pdf"))) == 1
    assert len(list(job_dir.glob("drawing-spec-review-v001-*.json"))) == 1


def test_manufacturing_review_preserves_an_active_cross_process_claim(client, settings):
    job_id, _drawing_spec = _create_manufacturing_job(client)
    job_dir = settings.data_dir / job_id
    claim = job_dir / "manufacturing-review-v001.claim"
    claim.write_text(
        json.dumps(
            {
                "claim_id": "active-other-process",
                "created_at": "2099-01-01T00:00:00+00:00",
                "lease_expires_at": 4_070_908_800,
            }
        ),
        encoding="utf-8",
    )

    response = _transition(
        client,
        job_id,
        action="submit",
        expected_version=0,
    )

    assert response.status_code == 409
    assert claim.exists()
    assert not list(job_dir.glob("drawing-review-v001-*.pdf"))
    assert not list(job_dir.glob("drawing-spec-review-v001-*.json"))


def test_non_manufacturing_generate_from_spec_remains_compatible(client):
    response = client.post(
        "/api/v1/generate-from-spec",
        json={
            "spec": _plate_spec(),
            "formats": ["json", "pdf"],
            "render": False,
            "backend": "source_only",
        },
    )
    assert response.status_code == 200, response.text
    names = {artifact["filename"] for artifact in response.json()["artifacts"]}
    assert "drawing.pdf" in names
    assert "drawing-spec.json" not in names
    assert not any(name.startswith("manufacturing-review-") for name in names)


def test_manufacturing_package_requires_pdf_before_creating_a_job(client):
    spec = _plate_spec()
    template = client.post(
        "/api/v1/manufacturing-template",
        json={"spec": spec},
    )
    assert template.status_code == 200, template.text
    before = client.get("/api/v1/jobs").json()

    response = client.post(
        "/api/v1/generate-from-spec",
        json={
            "spec": spec,
            "drawing_spec": template.json(),
            "formats": ["json"],
            "render": False,
        },
    )

    assert response.status_code == 422
    assert "requires pdf" in response.text
    assert client.get("/api/v1/jobs").json() == before


def test_async_generate_from_spec_preserves_manufacturing_drawing(client, settings):
    spec = _plate_spec()
    template = client.post(
        "/api/v1/manufacturing-template",
        json={"spec": spec},
    )
    assert template.status_code == 200, template.text
    queued = client.post(
        "/api/v1/async/generate-from-spec",
        json={
            "spec": spec,
            "drawing_spec": template.json(),
            "formats": ["json", "pdf"],
            "render": False,
            "backend": "source_only",
        },
    )
    assert queued.status_code == 202, queued.text

    worker = AsyncJobWorker(
        settings,
        queue=client.app.state.async_queue,
        worker_id="manufacturing-api-test-worker",
    )
    try:
        assert worker.process_next() is True
    finally:
        worker.close()

    status = client.get(
        f"/api/v1/async/jobs/{queued.json()['queue_job_id']}"
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "completed"
    job_id = status.json()["result_job_id"]
    review = client.get(f"/api/v1/jobs/{job_id}/manufacturing-review")
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "draft"
