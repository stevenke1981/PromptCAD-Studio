from __future__ import annotations

import io
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from app.services.image_analysis import ImageAnalysisError, ImageFeatureExtractor


def calibrated_plate_png() -> bytes:
    image = Image.new("L", (1000, 700), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 110, 900, 590), fill=0)
    for x, y in ((260, 270), (260, 430), (740, 270), (740, 430)):
        draw.ellipse((x - 20, y - 20, x + 20, y + 20), fill=255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def calibrated_plate_jpeg() -> bytes:
    image = Image.open(io.BytesIO(calibrated_plate_png()))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=98, subsampling=0)
    return buffer.getvalue()


def triangle_png() -> bytes:
    image = Image.new("L", (600, 500), 255)
    ImageDraw.Draw(image).polygon(((300, 60), (80, 440), (520, 440)), fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def trapezoid_png() -> bytes:
    image = Image.new("L", (700, 550), 255)
    ImageDraw.Draw(image).polygon(((120, 100), (580, 125), (620, 450), (80, 450)), fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def multipage_plate_pdf() -> bytes:
    first = Image.open(io.BytesIO(calibrated_plate_png())).convert("RGB")
    second = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(second)
    draw.rectangle((150, 150, 850, 550), fill="black")
    buffer = io.BytesIO()
    first.save(
        buffer,
        format="PDF",
        resolution=72,
        save_all=True,
        append_images=[second],
    )
    return buffer.getvalue()


def patent_multiview_png() -> bytes:
    image = Image.new("L", (1000, 700), 255)
    draw = ImageDraw.Draw(image)
    draw.text((80, 45), "FIG. 1", fill=0)
    draw.rectangle((60, 100, 450, 430), outline=0, width=6)
    draw.text((600, 45), "FIG. 2", fill=0)
    draw.rectangle((560, 100, 940, 430), outline=0, width=6)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def extractor():
    return ImageFeatureExtractor(
        max_bytes=2_000_000,
        max_pixels=2_000_000,
        max_dimension=2048,
    )


def test_calibrated_image_becomes_editable_feature_tree(extractor):
    result = extractor.analyze(
        calibrated_plate_png(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.convertible
    assert result.proposed_spec is not None
    assert result.validation is not None and result.validation.valid
    assert result.proposed_spec.base.kind == "plate"
    assert result.proposed_spec.base.length == pytest.approx(100, abs=0.1)
    assert result.proposed_spec.base.width == pytest.approx(60, abs=0.2)
    assert result.proposed_spec.base.thickness == 5
    assert len(result.circles) == 4
    assert len(result.proposed_spec.holes) == 4
    assert len(result.feature_tree) == 10
    assert result.proposed_spec.planner.review_required
    assert result.calibration.mm_per_pixel == pytest.approx(0.125, abs=0.001)

    centers = sorted((round(hole.x), round(hole.y)) for hole in result.proposed_spec.holes)
    assert centers == [(-30, -10), (-30, 10), (30, -10), (30, 10)]
    assert all(hole.diameter == pytest.approx(5, abs=0.3) for hole in result.proposed_spec.holes)


def test_image_analysis_is_deterministic(extractor):
    data = calibrated_plate_png()

    first = extractor.analyze(data, known_length_mm=100, thickness_mm=5)
    second = extractor.analyze(data, known_length_mm=100, thickness_mm=5)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_jpeg_is_decoded_and_converted(extractor):
    result = extractor.analyze(
        calibrated_plate_jpeg(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.image_format == "JPEG"
    assert result.convertible
    assert result.proposed_spec is not None
    assert len(result.proposed_spec.holes) == 4


def test_non_rectangular_profile_becomes_editable_profile_extrusion(extractor):
    result = extractor.analyze(
        triangle_png(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.convertible
    assert result.outer_profile.shape == "profile"
    assert len(result.outer_profile.points_mm) == 3
    assert result.proposed_spec is not None
    assert result.proposed_spec.schema_version == "1.1"
    assert result.proposed_spec.base.kind == "profile_extrusion"
    assert result.validation is not None and result.validation.valid
    assert result.feature_tree[0].operation == "sketch_profile"

    rebuilt = extractor.spec_from_feature_tree(
        result.feature_tree,
        image_sha256=result.image_sha256,
    )
    assert rebuilt.base.kind == "profile_extrusion"
    assert rebuilt.base.thickness == 5
    assert len(rebuilt.base.outer.segments) == 3


def test_perspective_trapezoid_is_not_converted(extractor):
    result = extractor.analyze(
        trapezoid_png(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert not result.convertible
    assert result.outer_profile.shape == "unsupported"


def test_opt_in_perspective_correction_rectifies_four_corner_plate(extractor):
    result = extractor.analyze(
        trapezoid_png(),
        known_length_mm=100,
        thickness_mm=5,
        perspective_correction=True,
    )

    assert result.convertible
    assert result.outer_profile.shape == "rectangle"
    assert result.outer_profile.perspective_corrected
    assert result.proposed_spec is not None
    assert result.proposed_spec.base.kind == "plate"
    assert any("透視校正" in warning for warning in result.warnings)


def test_pdf_page_is_rasterized_with_bounded_page_metadata(extractor):
    result = extractor.analyze(
        multipage_plate_pdf(),
        known_length_mm=100,
        thickness_mm=5,
        page_index=0,
    )

    assert result.convertible
    assert result.image_format == "PDF"
    assert result.source_kind == "pdf"
    assert result.source_page_index == 0
    assert result.source_page_count == 2
    assert result.proposed_spec is not None
    assert result.proposed_spec.base.kind == "plate"
    assert any("PDF 第 1 頁" in warning for warning in result.warnings)


def test_pdf_page_index_is_validated(extractor):
    with pytest.raises(ImageAnalysisError, match="outside 2 pages"):
        extractor.analyze(
            multipage_plate_pdf(),
            known_length_mm=100,
            thickness_mm=5,
            page_index=2,
        )


def test_pdf_page_count_limit_is_enforced():
    extractor = ImageFeatureExtractor(
        max_bytes=2_000_000,
        max_pixels=2_000_000,
        max_dimension=2048,
        max_pdf_pages=1,
    )

    with pytest.raises(ImageAnalysisError, match="exceeds the 1 page limit"):
        extractor.analyze(
            multipage_plate_pdf(),
            known_length_mm=100,
            thickness_mm=5,
        )


def test_pdfium_analysis_is_stable_across_threads(extractor):
    data = multipage_plate_pdf()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: extractor.analyze(
                    data,
                    known_length_mm=100,
                    thickness_mm=5,
                ),
                range(4),
            )
        )

    assert all(result.convertible for result in results)
    assert len({result.image_sha256 for result in results}) == 1


def test_pdfium_open_render_and_teardown_share_one_serial_lock(extractor):
    class CallTracker:
        def __init__(self) -> None:
            self._guard = threading.Lock()
            self.active = 0
            self.peak = 0

        def touch(self) -> None:
            with self._guard:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.005)
            with self._guard:
                self.active -= 1

    tracker = CallTracker()

    class FakeBitmap:
        def to_pil(self):
            tracker.touch()
            image = Image.new("L", (200, 120), 255)
            ImageDraw.Draw(image).rectangle((20, 20, 180, 100), fill=0)
            return image

        def close(self) -> None:
            tracker.touch()

    class FakePage:
        def get_size(self):
            tracker.touch()
            return (100.0, 60.0)

        def render(self, **_kwargs):
            tracker.touch()
            return FakeBitmap()

        def close(self) -> None:
            tracker.touch()

    class FakeDocument:
        def __init__(self, _data) -> None:
            tracker.touch()

        def __len__(self) -> int:
            tracker.touch()
            return 1

        def __getitem__(self, _index: int):
            tracker.touch()
            return FakePage()

        def close(self) -> None:
            tracker.touch()

    start = threading.Barrier(4)

    def analyze(_index: int):
        start.wait(timeout=2)
        return extractor.analyze(
            b"%PDF-controlled",
            known_length_mm=100,
            thickness_mm=5,
        )

    fake_pdfium = SimpleNamespace(PdfDocument=FakeDocument)
    with (
        patch.dict(sys.modules, {"pypdfium2": fake_pdfium}),
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        results = list(pool.map(analyze, range(4)))

    assert all(result.convertible for result in results)
    assert tracker.peak == 1


def test_pdf_upload_api_accepts_page_selection(client):
    response = client.post(
        "/api/v1/image-analysis",
        files={"image": ("drawing.pdf", multipage_plate_pdf(), "application/pdf")},
        data={"known_length_mm": "100", "thickness_mm": "5", "page_index": "0"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["convertible"] is True
    assert body["source_kind"] == "pdf"
    assert body["source_page_index"] == 0
    assert body["source_page_count"] == 2


def test_patent_api_exposes_ambiguous_views_and_accepts_explicit_selection(client):
    request = {
        "files": {"image": ("patent.png", patent_multiview_png(), "image/png")},
        "data": {
            "known_length_mm": "100",
            "thickness_mm": "5",
            "content_profile": "patent",
        },
    }
    blocked = client.post("/api/v1/image-analysis", **request)

    assert blocked.status_code == 200, blocked.text
    blocked_body = blocked.json()
    assert blocked_body["content_profile"] == "patent"
    assert blocked_body["ambiguous_objects"] is True
    assert blocked_body["convertible"] is False
    assert len(blocked_body["object_candidates"]) == 2

    request["data"]["object_index"] = "1"
    selected = client.post("/api/v1/image-analysis", **request)

    assert selected.status_code == 200, selected.text
    selected_body = selected.json()
    assert selected_body["selected_object_index"] == 1
    assert selected_body["convertible"] is True
    assert selected_body["proposed_spec"]["planner"]["review_required"] is True

    generated = client.post(
        "/api/v1/generate-from-image-feature-tree",
        json={
            "analysis": selected_body,
            "feature_tree": selected_body["feature_tree"],
            "formats": ["step", "pdf", "json"],
            "render": True,
            "backend": "cadquery",
        },
    )

    assert generated.status_code == 200, generated.text
    manifest = generated.json()
    assert manifest["status"] == "completed"
    assert manifest["planner_used"] == "image-feature-tree"
    assert {"model.step", "drawing.pdf", "image-analysis.json", "feature-tree.json"} <= {
        artifact["filename"] for artifact in manifest["artifacts"]
    }


def test_duplicate_free_profile_points_are_rejected(extractor):
    analysis = extractor.analyze(
        triangle_png(),
        known_length_mm=100,
        thickness_mm=5,
    )
    tree = [node.model_copy(deep=True) for node in analysis.feature_tree]
    tree[0].points[1] = tree[0].points[0]

    with pytest.raises(ImageAnalysisError, match="must be unique"):
        extractor.spec_from_feature_tree(
            tree,
            image_sha256=analysis.image_sha256,
        )


def test_dimension_limit_is_checked_before_exif_transpose(extractor):
    image = Image.new("L", (3000, 100), 255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    with (
        patch("app.services.image_analysis.ImageOps.exif_transpose") as transpose,
        pytest.raises(ImageAnalysisError, match="dimensions exceed"),
    ):
        extractor.analyze(buffer.getvalue(), known_length_mm=100, thickness_mm=5)

    transpose.assert_not_called()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not an image", "invalid"),
        (calibrated_plate_png()[:100], "invalid"),
    ],
)
def test_invalid_or_truncated_images_are_rejected(extractor, data, message):
    with pytest.raises(ImageAnalysisError, match=message):
        extractor.analyze(data, known_length_mm=100, thickness_mm=5)


def test_compressed_upload_size_is_bounded():
    extractor = ImageFeatureExtractor(max_bytes=100, max_pixels=2_000_000, max_dimension=2048)

    with pytest.raises(ImageAnalysisError, match="byte limit"):
        extractor.analyze(calibrated_plate_png(), known_length_mm=100, thickness_mm=5)


def test_image_api_returns_candidate_without_storing_untrusted_filename(client):
    response = client.post(
        "/api/v1/image-analysis",
        files={"image": ("../../private-design.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "100", "thickness_mm": "5"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["convertible"] is True
    assert body["proposed_spec"]["planner"]["planner"] == "image-feature-tree"
    assert len(body["feature_tree"]) == 10
    assert client.get("/api/v1/jobs").json() == []


def test_edited_feature_tree_converts_back_to_valid_cad_spec(client):
    analysis = client.post(
        "/api/v1/image-analysis",
        files={"image": ("plate.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "100", "thickness_mm": "5"},
    ).json()
    tree = analysis["feature_tree"]
    tree[0]["parameters"]["length_mm"] = 120

    response = client.post(
        "/api/v1/image-feature-tree-to-spec",
        json={"analysis": analysis, "feature_tree": tree},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spec"]["base"]["length"] == 120
    assert body["spec"]["planner"]["planner"] == "image-feature-tree"
    assert body["validation"]["valid"] is True


def test_browser_json_round_trip_preserves_signed_analysis(client):
    analysis = client.post(
        "/api/v1/image-analysis",
        files={"image": ("plate.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "100", "thickness_mm": "5"},
    ).json()
    assert analysis["outer_profile"]["rotation_deg"] == 0
    analysis["outer_profile"]["rotation_deg"] = 0

    response = client.post(
        "/api/v1/image-feature-tree-to-spec",
        json={"analysis": analysis, "feature_tree": analysis["feature_tree"]},
    )

    assert response.status_code == 200, response.text


def test_incomplete_feature_tree_is_rejected(client):
    analysis = client.post(
        "/api/v1/image-analysis",
        files={"image": ("plate.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "100", "thickness_mm": "5"},
    ).json()
    tree = [
        node
        for node in analysis["feature_tree"]
        if node["operation"] != "cut_through"
    ]

    response = client.post(
        "/api/v1/image-feature-tree-to-spec",
        json={"analysis": analysis, "feature_tree": tree},
    )

    assert response.status_code == 422
    assert "matching through cut" in response.json()["detail"]


def test_tampered_image_provenance_is_rejected(client):
    analysis = client.post(
        "/api/v1/image-analysis",
        files={"image": ("plate.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "100", "thickness_mm": "5"},
    ).json()
    analysis["image_sha256"] = "0" * 64

    response = client.post(
        "/api/v1/image-feature-tree-to-spec",
        json={"analysis": analysis, "feature_tree": analysis["feature_tree"]},
    )

    assert response.status_code == 422
    assert "provenance" in response.json()["detail"]


def test_tampered_pdf_page_provenance_is_rejected(client):
    analysis = client.post(
        "/api/v1/image-analysis",
        files={"image": ("drawing.pdf", multipage_plate_pdf(), "application/pdf")},
        data={"known_length_mm": "100", "thickness_mm": "5", "page_index": "0"},
    ).json()
    analysis["source_page_index"] = 1

    response = client.post(
        "/api/v1/image-feature-tree-to-spec",
        json={"analysis": analysis, "feature_tree": analysis["feature_tree"]},
    )

    assert response.status_code == 422
    assert "provenance" in response.json()["detail"]


def test_feature_tree_generation_preserves_analysis_artifacts(client):
    analysis = client.post(
        "/api/v1/image-analysis",
        files={"image": ("plate.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "100", "thickness_mm": "5"},
    ).json()

    response = client.post(
        "/api/v1/generate-from-image-feature-tree",
        json={
            "analysis": analysis,
            "feature_tree": analysis["feature_tree"],
            "formats": ["json", "py", "svg"],
            "render": False,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["planner_used"] == "image-feature-tree"
    names = {artifact["filename"] for artifact in body["artifacts"]}
    assert {"image-analysis.json", "feature-tree.json"} <= names


def test_image_api_rejects_non_finite_calibration(client):
    response = client.post(
        "/api/v1/image-analysis",
        files={"image": ("plate.png", calibrated_plate_png(), "image/png")},
        data={"known_length_mm": "NaN", "thickness_mm": "5"},
    )

    assert response.status_code == 422


def test_multipart_body_limit_rejects_before_image_decode(tmp_path):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        env="test",
        data_dir=tmp_path / "generated",
        planner_mode="rule",
        render_backend="source_only",
        max_image_bytes=100_000,
    )
    with TestClient(create_app(settings)) as limited:
        response = limited.post(
            "/api/v1/image-analysis",
            files={"image": ("huge.png", b"x" * 300_000, "image/png")},
            data={"known_length_mm": "100", "thickness_mm": "5"},
        )

    assert response.status_code == 413
