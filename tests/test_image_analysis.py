from __future__ import annotations

import io
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


def test_non_rectangular_profile_is_not_converted(extractor):
    result = extractor.analyze(
        triangle_png(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert not result.convertible
    assert result.outer_profile.shape == "unsupported"
    assert result.proposed_spec is None
    assert result.feature_tree == []


def test_perspective_trapezoid_is_not_converted(extractor):
    result = extractor.analyze(
        trapezoid_png(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert not result.convertible
    assert result.outer_profile.shape == "unsupported"


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
