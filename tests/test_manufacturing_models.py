from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.models.cad import (
    CadDocument,
    FilletFeature,
    HoleFeature,
    Material,
    PlannerMetadata,
    PlateBase,
    RectangularCutoutFeature,
    RingBase,
    SideFace,
)
from app.models.manufacturing import (
    BaseFaceDatumTarget,
    BomItem,
    DatumDefinition,
    DrawingDimension,
    FilletDimensionTarget,
    GeneralTolerance,
    HoleAxisDatumTarget,
    HoleDimensionTarget,
    HoleMeasurement,
    ManufacturingDrawingSpec,
    ProcurementType,
    ReviewAction,
    ReviewStatus,
    ReviewTransitionRequest,
    RevisionEntry,
    SurfaceFinishRequirement,
    SymmetricTolerance,
    TitleBlock,
)
from app.services.manufacturing import ManufacturingDrawingService


def plate_doc() -> CadDocument:
    return CadDocument(
        name="mounting-plate",
        source_prompt="120 x 60 x 10 plate",
        material=Material.ALUMINUM,
        base=PlateBase(length=120, width=60, thickness=10),
        holes=[HoleFeature(x=20, y=-10, diameter=6)],
        cutouts=[
            RectangularCutoutFeature(
                face=SideFace.POSITIVE_X,
                x=0,
                y=5,
                z=3,
                width=12,
                height=4,
            )
        ],
        fillets=[FilletFeature(radius=5)],
        planner=PlannerMetadata(planner="test"),
    )


def test_safe_default_is_bound_to_cad_and_resolves_nominals() -> None:
    doc = plate_doc()
    service = ManufacturingDrawingService()

    spec = service.create_default(doc, author="Engineer")
    resolved = service.resolve_dimensions(doc, spec)

    assert spec.schema_version == "1.0"
    assert spec.general_tolerance.kind == "bilateral"
    assert spec.general_tolerance.linear_mm == 0.2
    assert spec.review_status == ReviewStatus.DRAFT
    assert spec.review_version == 0
    assert spec.bom[0].material == "aluminum"
    assert [item.nominal for item in resolved] == [120, 60, 10, 6, 20, -10]
    assert [datum.id for datum in spec.datums] == ["A", "B", "C"]
    assert spec.surface_finishes[0].ra_micrometers == 3.2
    assert spec.cad_document_sha256 == service.cad_document_sha256(doc)


def test_dimension_contract_rejects_arbitrary_nominal_value() -> None:
    with pytest.raises(ValidationError, match="nominal"):
        DrawingDimension.model_validate(
            {
                "id": "overall-x",
                "target": {"kind": "base", "measurement": "overall_x"},
                "tolerance": {"kind": "reference"},
                "nominal": 999,
            }
        )


def test_resolve_all_supported_feature_targets_and_angle_unit() -> None:
    doc = plate_doc()
    service = ManufacturingDrawingService()
    dimensions = [
        DrawingDimension(
            id="hole-diameter",
            target=HoleDimensionTarget(index=0, measurement=HoleMeasurement.DIAMETER),
            tolerance=SymmetricTolerance(plus_minus_mm=0.05),
        ),
        DrawingDimension(
            id="fillet-radius",
            target=FilletDimensionTarget(index=0),
        ),
    ]

    assert service.resolve_dimension(doc, dimensions[0]).nominal == 6
    assert service.resolve_dimension(doc, dimensions[1]).nominal == 5


def test_cad_hash_prevents_using_annotations_with_modified_geometry() -> None:
    service = ManufacturingDrawingService()
    spec = service.create_default(plate_doc())
    changed = plate_doc().model_copy(
        update={"base": PlateBase(length=121, width=60, thickness=10)}
    )

    with pytest.raises(ValueError, match="different CAD document"):
        service.validate_against_cad(changed, spec)


def test_missing_feature_and_axisymmetric_planar_face_fail_closed() -> None:
    service = ManufacturingDrawingService()
    doc = plate_doc()
    missing = DrawingDimension(
        id="missing-hole",
        target=HoleDimensionTarget(index=1, measurement=HoleMeasurement.DIAMETER),
    )
    with pytest.raises(ValueError, match="missing hole"):
        service.resolve_dimension(doc, missing)

    ring = CadDocument(
        name="ring",
        source_prompt="ring",
        base=RingBase(outer_diameter=50, inner_diameter=30, height=8),
        planner=PlannerMetadata(planner="test"),
    )
    spec = service.create_default(ring)
    payload = spec.model_dump(mode="python")
    payload["datums"] = [
        DatumDefinition(
            id="A",
            target=BaseFaceDatumTarget(face="positive_x"),
        )
    ]
    unsafe = ManufacturingDrawingSpec.model_validate(payload)
    with pytest.raises(ValueError, match="axisymmetric"):
        service.validate_against_cad(ring, unsafe)


def test_unique_ids_and_cross_references_are_strict() -> None:
    service = ManufacturingDrawingService()
    spec = service.create_default(plate_doc())
    payload = spec.model_dump(mode="python")
    payload["dimensions"] = [payload["dimensions"][0], payload["dimensions"][0]]
    with pytest.raises(ValidationError, match="dimension ids must be unique"):
        ManufacturingDrawingSpec.model_validate(payload)

    payload = spec.model_dump(mode="python")
    payload["dimensions"][0]["datum_references"] = ["Z"]
    with pytest.raises(ValidationError, match="unknown datums"):
        ManufacturingDrawingSpec.model_validate(payload)


def test_general_tolerance_requires_complete_explicit_values() -> None:
    with pytest.raises(ValidationError, match="requires linear_mm and angular_deg"):
        GeneralTolerance(kind="bilateral", linear_mm=0.2)
    with pytest.raises(ValidationError, match="cannot contain numeric values"):
        GeneralTolerance(kind="unspecified", linear_mm=0.2)


def test_review_transition_uses_optimistic_version_and_terminal_states() -> None:
    service = ManufacturingDrawingService()
    spec = service.create_default(plate_doc())
    submitted = service.transition(
        spec,
        ReviewTransitionRequest(
            action=ReviewAction.SUBMIT,
            expected_version=0,
            reviewer="Checker",
            occurred_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
        ),
    )
    approved = service.transition(
        submitted,
        ReviewTransitionRequest(
            action=ReviewAction.APPROVE,
            expected_version=1,
            reviewer="Approver",
            occurred_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        ),
    )

    assert approved.review_status == ReviewStatus.APPROVED
    assert approved.review_version == 2
    assert [record.version for record in approved.review_records] == [1, 2]
    assert approved.title_block.checked_by == "Approver"
    assert approved.title_block.approved_by == "Approver"
    with pytest.raises(ValueError, match="terminal"):
        service.transition(
            approved,
            ReviewTransitionRequest(
                action=ReviewAction.SUBMIT,
                expected_version=2,
                reviewer="Someone",
            ),
        )


def test_review_transition_rejects_version_conflict_invalid_action_and_blank_rejection() -> None:
    service = ManufacturingDrawingService()
    spec = service.create_default(plate_doc())
    with pytest.raises(ValueError, match="version conflict"):
        service.transition(
            spec,
            ReviewTransitionRequest(
                action=ReviewAction.SUBMIT,
                expected_version=1,
                reviewer="Checker",
            ),
        )
    with pytest.raises(ValueError, match="not allowed"):
        service.transition(
            spec,
            ReviewTransitionRequest(
                action=ReviewAction.APPROVE,
                expected_version=0,
                reviewer="Checker",
            ),
        )
    submitted = service.transition(
        spec,
        ReviewTransitionRequest(
            action=ReviewAction.SUBMIT,
            expected_version=0,
            reviewer="Checker",
        ),
    )
    with pytest.raises(ValueError, match="requires a note"):
        service.transition(
            submitted,
            ReviewTransitionRequest(
                action=ReviewAction.REJECT,
                expected_version=1,
                reviewer="Checker",
            ),
        )


def test_spec_validates_datum_surface_bom_and_revision_references() -> None:
    service = ManufacturingDrawingService()
    doc = plate_doc()
    spec = service.create_default(doc)
    payload = spec.model_dump(mode="python")
    payload["datums"] = [DatumDefinition(id="A", target=HoleAxisDatumTarget(hole_index=0))]
    payload["surface_finishes"] = [
        SurfaceFinishRequirement(
            id="finish-1",
            target={"kind": "hole_wall", "hole_index": 0},
            ra_micrometers=1.6,
            datum_reference="A",
        )
    ]
    for dimension in payload["dimensions"]:
        dimension["datum_references"] = []
    payload["dimensions"][0]["datum_references"] = ["A"]
    enriched = ManufacturingDrawingSpec.model_validate(payload)

    service.validate_against_cad(doc, enriched)

    duplicate_bom = enriched.model_dump(mode="python")
    duplicate_bom["bom"] = [
        BomItem(
            id="item-1",
            item_number=1,
            part_number="P1",
            description="one",
            quantity=1,
            procurement=ProcurementType.MAKE,
        ),
        BomItem(
            id="item-2",
            item_number=1,
            part_number="P2",
            description="two",
            quantity=1,
            procurement=ProcurementType.BUY,
        ),
    ]
    with pytest.raises(ValidationError, match="item_numbers"):
        ManufacturingDrawingSpec.model_validate(duplicate_bom)


def test_title_revision_and_review_record_reference_must_match() -> None:
    service = ManufacturingDrawingService()
    spec = service.create_default(plate_doc())
    payload = spec.model_dump(mode="python")
    payload["title_block"] = TitleBlock(
        part_name="part",
        part_number="P1",
        drawing_number="D1",
        revision="B",
        drawn_by="Engineer",
        drawn_on=date(2026, 8, 1),
    )
    with pytest.raises(ValidationError, match="latest revision"):
        ManufacturingDrawingSpec.model_validate(payload)

    payload = spec.model_dump(mode="python")
    payload["revisions"] = [
        RevisionEntry(
            revision="A",
            occurred_on=date(2026, 8, 1),
            description="draft",
            author="Engineer",
            review_record_id="unknown-record",
        )
    ]
    with pytest.raises(ValidationError, match="unknown review record"):
        ManufacturingDrawingSpec.model_validate(payload)
