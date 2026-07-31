from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime

from app.models.cad import (
    CadDocument,
    CylinderBase,
    EnclosureBase,
    LBracketBase,
    PlateBase,
    ProfileExtrusionBase,
    ProfileRevolutionBase,
    RingBase,
)
from app.models.manufacturing import (
    BaseDimensionTarget,
    BaseFaceDatumTarget,
    BaseFaceSurfaceTarget,
    BaseMeasurement,
    BomItem,
    ChamferDimensionTarget,
    CutoutDimensionTarget,
    CutoutWallSurfaceTarget,
    DatumDefinition,
    DatumFace,
    DrawingDimension,
    FilletDimensionTarget,
    GeneralTolerance,
    HoleAxisDatumTarget,
    HoleDimensionTarget,
    HoleMeasurement,
    HoleWallSurfaceTarget,
    ManufacturingDrawingSpec,
    ProcurementType,
    ReferenceTolerance,
    ResolvedDimension,
    ReviewAction,
    ReviewRecord,
    ReviewStatus,
    ReviewTransitionRequest,
    RevisionEntry,
    SurfaceFinishRequirement,
    TitleBlock,
)
from app.services.profile_geometry import loop_bounds


class ManufacturingDrawingService:
    """Bind manufacturing annotations to a specific immutable CAD document."""

    @staticmethod
    def cad_document_sha256(doc: CadDocument) -> str:
        payload = json.dumps(
            doc.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def create_default(
        self,
        doc: CadDocument,
        *,
        part_number: str | None = None,
        drawing_number: str | None = None,
        author: str = "PromptCAD",
    ) -> ManufacturingDrawingSpec:
        safe_name = self._safe_number(doc.name)
        part_number = part_number or f"PC-{safe_name}"
        drawing_number = drawing_number or f"DWG-{safe_name}"
        dimensions = [
            DrawingDimension(
                id=f"overall-{axis}",
                target=BaseDimensionTarget(measurement=measurement),
                tolerance=ReferenceTolerance(),
                label=f"Overall {axis.upper()}",
            )
            for axis, measurement in (
                ("x", BaseMeasurement.OVERALL_X),
                ("y", BaseMeasurement.OVERALL_Y),
                ("z", BaseMeasurement.OVERALL_Z),
            )
        ]
        if isinstance(doc.base, RingBase):
            dimensions.append(
                DrawingDimension(
                    id="inner-diameter",
                    target=BaseDimensionTarget(measurement=BaseMeasurement.INNER_DIAMETER),
                    tolerance=ReferenceTolerance(),
                    label="Inner diameter",
                )
            )
        if isinstance(doc.base, EnclosureBase):
            dimensions.append(
                DrawingDimension(
                    id="wall-thickness",
                    target=BaseDimensionTarget(measurement=BaseMeasurement.WALL_THICKNESS),
                    tolerance=ReferenceTolerance(),
                    label="Wall thickness",
                )
            )
        axisymmetric = isinstance(doc.base, (CylinderBase, RingBase, ProfileRevolutionBase))
        datums = [
            DatumDefinition(
                id="A",
                target=BaseFaceDatumTarget(face=DatumFace.BOTTOM),
                description="Primary mounting face",
            )
        ]
        if axisymmetric:
            if doc.holes:
                datums.append(
                    DatumDefinition(
                        id="B",
                        target=HoleAxisDatumTarget(hole_index=0),
                        description="First hole axis",
                    )
                )
        else:
            datums.extend(
                [
                    DatumDefinition(
                        id="B",
                        target=BaseFaceDatumTarget(face=DatumFace.NEGATIVE_X),
                        description="Secondary X origin face",
                    ),
                    DatumDefinition(
                        id="C",
                        target=BaseFaceDatumTarget(face=DatumFace.NEGATIVE_Y),
                        description="Tertiary Y origin face",
                    ),
                ]
            )
        datum_ids = {datum.id for datum in datums}
        for index, _hole in enumerate(doc.holes[:2]):
            dimensions.extend(
                [
                    DrawingDimension(
                        id=f"hole-{index + 1}-diameter",
                        target=HoleDimensionTarget(
                            index=index,
                            measurement=HoleMeasurement.DIAMETER,
                        ),
                        tolerance=ReferenceTolerance(),
                        label=f"Hole {index + 1} diameter",
                    ),
                    DrawingDimension(
                        id=f"hole-{index + 1}-x",
                        target=HoleDimensionTarget(index=index, measurement=HoleMeasurement.X),
                        tolerance=ReferenceTolerance(),
                        datum_references=["B"] if "B" in datum_ids and not axisymmetric else [],
                        label=f"Hole {index + 1} X",
                    ),
                    DrawingDimension(
                        id=f"hole-{index + 1}-y",
                        target=HoleDimensionTarget(index=index, measurement=HoleMeasurement.Y),
                        tolerance=ReferenceTolerance(),
                        datum_references=["C"] if "C" in datum_ids else [],
                        label=f"Hole {index + 1} Y",
                    ),
                ]
            )
        material = doc.material.value if doc.material else None
        spec = ManufacturingDrawingSpec(
            cad_document_sha256=self.cad_document_sha256(doc),
            title_block=TitleBlock(
                part_name=doc.name,
                part_number=part_number,
                drawing_number=drawing_number,
                revision="A",
                drawn_by=author,
                drawn_on=date.today(),
            ),
            general_tolerance=GeneralTolerance(
                kind="bilateral",
                linear_mm=0.2,
                angular_deg=1.0,
                note="Template defaults; engineering review required before manufacturing",
            ),
            dimensions=dimensions,
            datums=datums,
            surface_finishes=[
                SurfaceFinishRequirement(
                    id="finish-1",
                    target=BaseFaceSurfaceTarget(face=DatumFace.TOP),
                    ra_micrometers=3.2,
                    datum_reference="A",
                    note="Template default; verify process capability",
                )
            ],
            bom=[
                BomItem(
                    id="item-1",
                    item_number=1,
                    part_number=part_number,
                    description=doc.name,
                    quantity=1,
                    procurement=ProcurementType.MAKE,
                    material=material,
                )
            ],
            revisions=[
                RevisionEntry(
                    revision="A",
                    occurred_on=date.today(),
                    description="Initial manufacturing drawing draft",
                    author=author,
                )
            ],
        )
        self.validate_against_cad(doc, spec)
        return spec

    def validate_against_cad(self, doc: CadDocument, spec: ManufacturingDrawingSpec) -> None:
        if spec.cad_document_sha256 != self.cad_document_sha256(doc):
            raise ValueError("manufacturing drawing is bound to a different CAD document")
        self.resolve_dimensions(doc, spec)
        for datum in spec.datums:
            self._validate_datum_target(doc, datum)
        for finish in spec.surface_finishes:
            target = finish.target
            if isinstance(target, HoleWallSurfaceTarget) and target.hole_index >= len(doc.holes):
                raise ValueError("surface finish references a missing hole")
            if isinstance(target, CutoutWallSurfaceTarget) and target.cutout_index >= len(doc.cutouts):
                raise ValueError("surface finish references a missing cutout")
            if isinstance(target, BaseFaceSurfaceTarget):
                self._validate_base_face(doc, target.face.value)

    def resolve_dimensions(
        self,
        doc: CadDocument,
        spec: ManufacturingDrawingSpec,
    ) -> list[ResolvedDimension]:
        if spec.cad_document_sha256 != self.cad_document_sha256(doc):
            raise ValueError("manufacturing drawing is bound to a different CAD document")
        return [self.resolve_dimension(doc, dimension) for dimension in spec.dimensions]

    def resolve_dimension(
        self,
        doc: CadDocument,
        dimension: DrawingDimension,
    ) -> ResolvedDimension:
        target = dimension.target
        unit = "mm"
        if isinstance(target, BaseDimensionTarget):
            nominal = self._resolve_base_dimension(doc, target.measurement)
        elif isinstance(target, HoleDimensionTarget):
            if target.index >= len(doc.holes):
                raise ValueError("dimension references a missing hole")
            value = getattr(doc.holes[target.index], target.measurement.value)
            if value is None:
                raise ValueError(f"hole has no {target.measurement.value} value")
            nominal = float(value)
            if target.measurement == HoleMeasurement.COUNTERSINK_ANGLE:
                unit = "deg"
        elif isinstance(target, CutoutDimensionTarget):
            if target.index >= len(doc.cutouts):
                raise ValueError("dimension references a missing cutout")
            nominal = float(getattr(doc.cutouts[target.index], target.measurement.value))
        elif isinstance(target, FilletDimensionTarget):
            if target.index >= len(doc.fillets):
                raise ValueError("dimension references a missing fillet")
            nominal = doc.fillets[target.index].radius
        elif isinstance(target, ChamferDimensionTarget):
            if target.index >= len(doc.chamfers):
                raise ValueError("dimension references a missing chamfer")
            nominal = doc.chamfers[target.index].distance
        else:  # pragma: no cover - discriminated union is exhaustive
            raise TypeError("unsupported dimension target")
        return ResolvedDimension(
            id=dimension.id,
            target=dimension.target,
            nominal=nominal,
            unit=unit,
            tolerance=dimension.tolerance,
            datum_references=dimension.datum_references,
            label=dimension.label,
            note=dimension.note,
            critical=dimension.critical,
        )

    def transition(
        self,
        spec: ManufacturingDrawingSpec,
        request: ReviewTransitionRequest,
    ) -> ManufacturingDrawingSpec:
        if request.expected_version != spec.review_version:
            raise ValueError("review version conflict")
        if spec.review_status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            raise ValueError("approved and rejected drawings are terminal")
        allowed = {
            (ReviewStatus.DRAFT, ReviewAction.SUBMIT): ReviewStatus.IN_REVIEW,
            (ReviewStatus.IN_REVIEW, ReviewAction.APPROVE): ReviewStatus.APPROVED,
            (ReviewStatus.IN_REVIEW, ReviewAction.REJECT): ReviewStatus.REJECTED,
        }
        next_status = allowed.get((spec.review_status, request.action))
        if next_status is None:
            raise ValueError("review action is not allowed from the current status")
        if request.action == ReviewAction.REJECT and not request.note.strip():
            raise ValueError("rejection requires a note")
        version = spec.review_version + 1
        record = ReviewRecord(
            id=request.record_id or f"review-{version}",
            version=version,
            action=request.action,
            from_status=spec.review_status,
            to_status=next_status,
            reviewer=request.reviewer,
            occurred_at=request.occurred_at or datetime.now(UTC),
            note=request.note,
        )
        title = spec.title_block.model_dump(mode="python")
        if request.action in {ReviewAction.APPROVE, ReviewAction.REJECT}:
            title["checked_by"] = request.reviewer
        if request.action == ReviewAction.APPROVE:
            title["approved_by"] = request.reviewer
        payload = spec.model_dump(mode="python")
        payload.update(
            title_block=title,
            review_status=next_status,
            review_version=version,
            review_records=[*spec.review_records, record],
        )
        return ManufacturingDrawingSpec.model_validate(payload)

    @staticmethod
    def _safe_number(value: str) -> str:
        compact = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
        return (compact or "PART")[:72]

    @staticmethod
    def _base_extents(doc: CadDocument) -> tuple[float, float, float]:
        base = doc.base
        if isinstance(base, PlateBase):
            return base.length, base.width, base.thickness
        if isinstance(base, CylinderBase):
            return base.diameter, base.diameter, base.height
        if isinstance(base, RingBase):
            return base.outer_diameter, base.outer_diameter, base.height
        if isinstance(base, LBracketBase):
            return base.width, base.depth, base.vertical_height + base.thickness
        if isinstance(base, EnclosureBase):
            return base.length, base.width, base.height
        if isinstance(base, ProfileExtrusionBase):
            min_x, min_y, max_x, max_y = loop_bounds(base.outer)
            return max_x - min_x, max_y - min_y, base.thickness
        if isinstance(base, ProfileRevolutionBase):
            _, min_z, max_radius, max_z = loop_bounds(base.outer)
            return max_radius * 2, max_radius * 2, max_z - min_z
        raise TypeError("unsupported CAD base")

    def _resolve_base_dimension(self, doc: CadDocument, measurement: BaseMeasurement) -> float:
        x, y, z = self._base_extents(doc)
        if measurement == BaseMeasurement.OVERALL_X:
            return x
        if measurement == BaseMeasurement.OVERALL_Y:
            return y
        if measurement == BaseMeasurement.OVERALL_Z:
            return z
        if measurement == BaseMeasurement.INNER_DIAMETER:
            if not isinstance(doc.base, RingBase):
                raise ValueError("inner_diameter is only available for a ring base")
            return doc.base.inner_diameter
        if measurement == BaseMeasurement.WALL_THICKNESS:
            if not isinstance(doc.base, EnclosureBase):
                raise ValueError("wall_thickness is only available for an enclosure base")
            return doc.base.wall_thickness
        raise TypeError("unsupported base measurement")

    def _validate_datum_target(self, doc: CadDocument, datum: DatumDefinition) -> None:
        target = datum.target
        if isinstance(target, HoleAxisDatumTarget) and target.hole_index >= len(doc.holes):
            raise ValueError("datum references a missing hole")
        if isinstance(target, BaseFaceDatumTarget):
            self._validate_base_face(doc, target.face.value)

    @staticmethod
    def _validate_base_face(doc: CadDocument, face: str) -> None:
        if isinstance(doc.base, (CylinderBase, RingBase, ProfileRevolutionBase)) and face in {
            "positive_x",
            "negative_x",
            "positive_y",
            "negative_y",
        }:
            raise ValueError("axisymmetric bases do not have planar X/Y datum faces")
