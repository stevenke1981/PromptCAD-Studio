from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.models.cad import ValidationReport
from app.services.image_analysis import ImageAnalysisError, ImageFeatureExtractor


@pytest.fixture
def extractor() -> ImageFeatureExtractor:
    return ImageFeatureExtractor(
        max_bytes=2_000_000,
        max_pixels=2_000_000,
        max_dimension=2048,
    )


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _photo() -> bytes:
    background = np.tile(np.linspace(235, 190, 800, dtype=np.uint8), (600, 1))
    image = Image.fromarray(background, mode="L")
    draw = ImageDraw.Draw(image)
    draw.polygon(((120, 95), (690, 125), (735, 510), (75, 485)), fill=45)
    draw.ellipse((250, 250, 300, 300), fill=220)
    return _png(image)


def _line_art(*, inverted: bool = False) -> bytes:
    background, foreground = (25, 235) if inverted else (255, 0)
    image = Image.new("L", (800, 600), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), outline=foreground, width=7)
    for x, y in ((260, 300), (540, 300)):
        draw.ellipse((x - 28, y - 28, x + 28, y + 28), outline=foreground, width=7)
    return _png(image)


def _patent_views() -> bytes:
    image = Image.new("L", (1000, 700), 255)
    draw = ImageDraw.Draw(image)
    draw.text((80, 45), "FIG. 1", fill=0)
    draw.rectangle((60, 100, 450, 430), outline=0, width=6)
    draw.text((600, 45), "FIG. 2", fill=0)
    draw.rectangle((560, 100, 940, 430), outline=0, width=6)
    return _png(image)


def _scan() -> bytes:
    rng = np.random.default_rng(7)
    pixels = rng.normal(242, 4, (600, 800)).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(pixels, mode="L")
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 110, 690, 490), fill=35)
    draw.ellipse((365, 265, 435, 335), fill=242)
    return _png(image)


def _auto_plate() -> bytes:
    image = Image.new("L", (700, 500), 250)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((80, 80, 620, 420), radius=18, fill=20)
    draw.ellipse((325, 215, 375, 265), fill=250)
    return _png(image)


def _auto_plate_with_arc(degrees: int) -> bytes:
    image = Image.new("L", (800, 600), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), fill=25)
    draw.arc((300, 220, 500, 420), start=0, end=degrees, fill=225, width=10)
    return _png(image)


def _auto_plate_with_glare() -> bytes:
    image = Image.new("L", (800, 600), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), fill=25)
    draw.polygon(((170, 100), (260, 100), (520, 500), (430, 500)), fill=210)
    return _png(image)


def _auto_plate_with_circle_annotation() -> bytes:
    image = Image.new("L", (800, 600), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), fill=25)
    draw.ellipse((350, 250, 450, 350), outline=225, width=10)
    return _png(image)


def _thick_ring_sketch() -> bytes:
    image = Image.new("L", (800, 600), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), outline=0, width=7)
    draw.ellipse((360, 260, 440, 340), outline=0, width=14)
    return _png(image)


def test_photo_profile_rectifies_one_perspective_object(extractor):
    result = extractor.analyze(
        _photo(),
        known_length_mm=120,
        thickness_mm=6,
        content_profile="photo",
        perspective_correction=True,
    )

    assert result.content_profile == "photo"
    assert result.convertible
    assert len(result.object_candidates) == 1
    assert result.selected_object_index == 0
    assert result.outer_profile.perspective_corrected


@pytest.mark.parametrize(
    ("profile", "inverted"),
    [("sketch", False), ("whiteboard", True)],
)
def test_line_art_profiles_recover_disconnected_circle_holes(
    extractor,
    profile,
    inverted,
):
    result = extractor.analyze(
        _line_art(inverted=inverted),
        known_length_mm=100,
        thickness_mm=5,
        content_profile=profile,
        accept_line_art_holes=True,
    )

    assert result.convertible
    assert result.content_profile == profile
    assert len(result.circles) == 2
    assert all(circle.extraction_method == "line_art_candidate" for circle in result.circles)
    assert all(circle.accepted_for_cad for circle in result.circles)
    assert all(circle.diameter_min_mm < circle.diameter_max_mm for circle in result.circles)
    assert len(result.proposed_spec.holes) == 2
    assert [node.operation for node in result.feature_tree].count("sketch_circle") == 2


def test_patent_multiview_fails_closed_until_object_is_selected(extractor):
    blocked = extractor.analyze(
        _patent_views(),
        known_length_mm=100,
        thickness_mm=5,
        content_profile="patent",
        perspective_correction=True,
    )

    assert blocked.ambiguous_objects
    assert not blocked.convertible
    assert blocked.selected_object_index is None
    assert len(blocked.object_candidates) == 2
    assert blocked.feature_tree == []
    assert any("object_index" in warning for warning in blocked.warnings)
    assert any("未執行透視校正" in warning for warning in blocked.warnings)

    selected = extractor.analyze(
        _patent_views(),
        known_length_mm=100,
        thickness_mm=5,
        content_profile="patent",
        object_index=1,
    )

    assert not selected.ambiguous_objects
    assert selected.convertible
    assert selected.selected_object_index == 1
    assert len(selected.object_candidates) == 2
    assert selected.proposed_spec is not None


def test_scan_profile_handles_bounded_noise_and_one_hole(extractor):
    result = extractor.analyze(
        _scan(),
        known_length_mm=100,
        thickness_mm=5,
        content_profile="scan",
    )

    assert result.content_profile == "scan"
    assert result.convertible
    assert len(result.object_candidates) == 1
    assert len(result.circles) == 1


def test_auto_profile_remains_backward_compatible(extractor):
    result = extractor.analyze(
        _auto_plate(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.content_profile == "auto"
    assert result.convertible
    assert result.selected_object_index == 0


@pytest.mark.parametrize("degrees", [180, 240])
def test_auto_profile_does_not_turn_open_arc_annotations_into_holes(
    extractor,
    degrees,
):
    result = extractor.analyze(
        _auto_plate_with_arc(degrees),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.convertible
    assert result.circles == []
    assert result.proposed_spec.holes == []


@pytest.mark.parametrize(
    "source",
    [_auto_plate_with_glare, _auto_plate_with_circle_annotation],
)
def test_auto_profile_does_not_turn_glare_or_ring_annotation_into_holes(
    extractor,
    source,
):
    result = extractor.analyze(
        source(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.circles == []


def test_thick_stroke_ring_requires_explicit_acceptance_and_is_not_duplicated(extractor):
    candidate = extractor.analyze(
        _thick_ring_sketch(),
        known_length_mm=100,
        thickness_mm=5,
        content_profile="sketch",
    )

    assert candidate.convertible
    assert len(candidate.circles) == 1
    assert candidate.circles[0].extraction_method == "line_art_candidate"
    assert not candidate.circles[0].accepted_for_cad
    assert candidate.proposed_spec.holes == []
    assert any("未明確接受" in warning for warning in candidate.warnings)

    accepted = extractor.analyze(
        _thick_ring_sketch(),
        known_length_mm=100,
        thickness_mm=5,
        content_profile="sketch",
        accept_line_art_holes=True,
    )
    assert accepted.validation is not None and accepted.validation.valid
    assert len(accepted.circles) == 1
    assert accepted.circles[0].accepted_for_cad
    assert len(accepted.proposed_spec.holes) == 1


def test_invalid_candidate_is_never_marked_convertible(extractor, monkeypatch):
    monkeypatch.setattr(
        extractor.validator,
        "validate",
        lambda _spec: ValidationReport(valid=False, review_required=True),
    )

    result = extractor.analyze(
        _auto_plate(),
        known_length_mm=100,
        thickness_mm=5,
    )

    assert result.proposed_spec is not None
    assert result.validation is not None and not result.validation.valid
    assert not result.convertible


def test_object_index_outside_visible_candidates_is_rejected(extractor):
    with pytest.raises(ImageAnalysisError, match="outside 2 detected objects"):
        extractor.analyze(
            _patent_views(),
            known_length_mm=100,
            thickness_mm=5,
            content_profile="patent",
            object_index=2,
        )
