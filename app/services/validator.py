from __future__ import annotations

import math

from app.models.cad import (
    Axis,
    CadDocument,
    CylinderBase,
    EnclosureBase,
    HoleFeature,
    HoleType,
    LBracketBase,
    PlateBase,
    RingBase,
    SideFace,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)


class DesignValidator:
    def validate(self, doc: CadDocument) -> ValidationReport:
        issues: list[ValidationIssue] = []
        self._base_checks(doc, issues)
        self._hole_checks(doc, issues)
        self._cutout_checks(doc, issues)
        self._standard_checks(doc, issues)
        self._edge_checks(doc, issues)

        for assumption in doc.assumptions:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    code="assumption",
                    message=assumption,
                )
            )

        valid = not any(issue.severity == ValidationSeverity.ERROR for issue in issues)
        review = (
            doc.planner.review_required
            or bool(doc.assumptions)
            or any(
                issue.severity in {ValidationSeverity.ERROR, ValidationSeverity.WARNING}
                for issue in issues
            )
        )
        return ValidationReport(valid=valid, review_required=review, issues=issues)

    def _base_checks(self, doc: CadDocument, issues: list[ValidationIssue]) -> None:
        base = doc.base
        if isinstance(base, EnclosureBase) and base.wall_thickness < 0.8:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="thin_wall",
                    message=f"外殼壁厚 {base.wall_thickness:g} mm 偏薄，需依材料與製程覆核。",
                )
            )
        if isinstance(base, LBracketBase) and base.thickness < 2:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="thin_bracket",
                    message="L 型支架板厚小於 2 mm，承載能力可能不足。",
                )
            )
        dimensions = self._dimensions(base)
        if max(dimensions) / min(dimensions) > 1000:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="extreme_aspect_ratio",
                    message="零件長寬高比例極端，請確認單位是否正確。",
                )
            )

    def _hole_checks(self, doc: CadDocument, issues: list[ValidationIssue]) -> None:
        for index, hole in enumerate(doc.holes):
            self._thread_check(hole, index, issues)
            self._hole_boundary_check(doc, hole, index, issues)
            self._hole_depth_check(doc, hole, index, issues)

        for first_index, first in enumerate(doc.holes):
            for second_index, second in enumerate(
                doc.holes[first_index + 1 :],
                start=first_index + 1,
            ):
                if first.axis != second.axis:
                    continue
                first_a, first_b = self._plane_coordinates(first)
                second_a, second_b = self._plane_coordinates(second)
                distance = math.hypot(first_a - second_a, first_b - second_b)
                required = (
                    self._effective_diameter(first) + self._effective_diameter(second)
                ) / 2
                if distance < required:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="overlapping_holes",
                            message=f"孔 {first_index + 1} 與孔 {second_index + 1} 發生重疊。",
                            feature_index=first_index,
                        )
                    )

    @staticmethod
    def _thread_check(
        hole: HoleFeature,
        index: int,
        issues: list[ValidationIssue],
    ) -> None:
        if hole.thread:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    code="thread_annotation_only",
                    message=f"{hole.thread} 目前以圓柱孔近似，未建立實體螺旋牙。",
                    feature_index=index,
                )
            )
        elif hole.hole_type == HoleType.TAPPED:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="tapped_hole_without_thread",
                    message=f"孔 {index + 1} 標記為攻牙孔，但沒有螺紋規格。",
                    feature_index=index,
                )
            )

    def _hole_boundary_check(
        self,
        doc: CadDocument,
        hole: HoleFeature,
        index: int,
        issues: list[ValidationIssue],
    ) -> None:
        radius = self._effective_diameter(hole) / 2
        base = doc.base

        if hole.axis == Axis.Z and isinstance(base, (CylinderBase, RingBase)):
            center_radius = math.hypot(hole.x, hole.y)
            outer_radius = (
                base.diameter / 2 if isinstance(base, CylinderBase) else base.outer_diameter / 2
            )
            clearance = outer_radius - center_radius - radius
            if clearance < 0:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="hole_outside_part",
                        message=f"孔 {index + 1} 超出圓形外輪廓。",
                        feature_index=index,
                    )
                )
                return
            if isinstance(base, RingBase):
                inner_radius = base.inner_diameter / 2
                if center_radius + radius <= inner_radius:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="hole_in_inner_void",
                            message=f"孔 {index + 1} 完全位於圓環內孔空間。",
                            feature_index=index,
                        )
                    )
                elif center_radius - radius < inner_radius:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            code="hole_intersects_inner_void",
                            message=f"孔 {index + 1} 與圓環內孔相交，會形成開口。",
                            feature_index=index,
                        )
                    )
        else:
            span_a, span_b, center_a, center_b = self._axis_plane(doc, hole.axis)
            point_a, point_b = self._plane_coordinates(hole)
            edge_a = span_a / 2 - abs(point_a - center_a) - radius
            edge_b = span_b / 2 - abs(point_b - center_b) - radius
            clearance = min(edge_a, edge_b)
            if clearance < 0:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="hole_outside_part",
                        message=f"孔 {index + 1} 超出零件在 {hole.axis.value.upper()} 軸法向平面的邊界。",
                        feature_index=index,
                    )
                )
                return

        if clearance < max(1.0, self._effective_diameter(hole) * 0.25):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="low_edge_clearance",
                    message=f"孔 {index + 1} 到邊緣的最小淨距僅 {clearance:.2f} mm。",
                    feature_index=index,
                )
            )

    def _hole_depth_check(
        self,
        doc: CadDocument,
        hole: HoleFeature,
        index: int,
        issues: list[ValidationIssue],
    ) -> None:
        material_depth = self._material_depth(doc, hole)
        if (
            hole.hole_type == HoleType.BLIND
            and hole.depth is not None
            and hole.depth >= material_depth
        ):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="blind_depth_exceeds_material",
                    message=(
                        f"孔 {index + 1} 盲孔深度 {hole.depth:g} mm 不小於估計材料厚度 "
                        f"{material_depth:g} mm。"
                    ),
                    feature_index=index,
                )
            )
        if (
            hole.hole_type == HoleType.COUNTERBORE
            and hole.counterbore_depth is not None
            and hole.counterbore_depth >= material_depth
        ):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="counterbore_depth_exceeds_material",
                    message=f"孔 {index + 1} 沉孔深度不小於估計材料厚度。",
                    feature_index=index,
                )
            )
        if (
            hole.hole_type == HoleType.COUNTERSINK
            and hole.countersink_diameter is not None
            and hole.countersink_angle is not None
        ):
            depth = (hole.countersink_diameter - hole.diameter) / (
                2 * max(0.01, math.tan(math.radians(hole.countersink_angle / 2)))
            )
            if depth >= material_depth:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="countersink_depth_exceeds_material",
                        message=f"孔 {index + 1} 的沉頭幾何深度不小於估計材料厚度。",
                        feature_index=index,
                    )
                )

    def _edge_checks(self, doc: CadDocument, issues: list[ValidationIssue]) -> None:
        dim_x, dim_y, dim_z = self._dimensions(doc.base)

        def safe_limit(selector) -> float:
            # Vertical edges round the XY outline and are not limited by the part thickness.
            if selector.value == "vertical":
                return min(dim_x, dim_y) / 2
            return min(dim_x, dim_y, dim_z) / 2

        for index, fillet in enumerate(doc.fillets):
            safe = safe_limit(fillet.selector)
            if fillet.radius >= safe:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="fillet_too_large",
                        message=f"圓角 R{fillet.radius:g} 大於所選邊可用尺寸的一半。",
                        feature_index=index,
                    )
                )
        for index, chamfer in enumerate(doc.chamfers):
            safe = safe_limit(chamfer.selector)
            if chamfer.distance >= safe:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="chamfer_too_large",
                        message=f"倒角 C{chamfer.distance:g} 大於所選邊可用尺寸的一半。",
                        feature_index=index,
                    )
                )

    @staticmethod
    def _cutout_checks(doc: CadDocument, issues: list[ValidationIssue]) -> None:
        for index, cutout in enumerate(doc.cutouts):
            if not isinstance(doc.base, EnclosureBase):
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="cutout_requires_enclosure",
                        message=f"矩形切口 {index + 1} 目前僅支援開放式外殼。",
                        feature_index=index,
                    )
                )
                continue

            if cutout.face in {SideFace.POSITIVE_X, SideFace.NEGATIVE_X}:
                horizontal_span = doc.base.width
                horizontal_center = cutout.y
            else:
                horizontal_span = doc.base.length
                horizontal_center = cutout.x

            horizontal_clearance = (
                horizontal_span / 2 - abs(horizontal_center) - cutout.width / 2
            )
            bottom_clearance = cutout.z - cutout.height / 2
            top_clearance = doc.base.height - cutout.z - cutout.height / 2
            if min(horizontal_clearance, bottom_clearance, top_clearance) < 0:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="cutout_outside_face",
                        message=f"矩形切口 {index + 1} 超出 {cutout.face.value} 側面邊界。",
                        feature_index=index,
                    )
                )

    @staticmethod
    def _standard_checks(doc: CadDocument, issues: list[ValidationIssue]) -> None:
        if not any(reference.key == "nema17-face" for reference in doc.standards):
            return
        if not isinstance(doc.base, LBracketBase):
            matches = False
        else:
            center_z = doc.base.thickness + doc.base.vertical_height / 2
            expected_raw = {
                (-15.5, center_z - 15.5),
                (15.5, center_z - 15.5),
                (15.5, center_z + 15.5),
                (-15.5, center_z + 15.5),
            }
            expected = {(round(x, 3), round(z, 3)) for x, z in expected_raw}
            motor_holes = [
                hole
                for hole in doc.holes
                if hole.axis == Axis.Y and hole.thread == "M3"
            ]
            actual = {(round(hole.x, 3), round(hole.z, 3)) for hole in motor_holes}
            center_holes = [
                hole
                for hole in doc.holes
                if hole.axis == Axis.Y
                and hole.thread is None
                and abs(hole.x) < 0.01
                and abs(hole.z - center_z) < 0.01
                and abs(hole.diameter - 22.5) < 0.05
            ]
            matches = (
                len(motor_holes) == 4
                and actual == expected
                and all(abs(hole.diameter - 3.4) < 0.05 for hole in motor_holes)
                and all(
                    hole.hole_type == HoleType.CLEARANCE and hole.depth is None
                    for hole in motor_holes
                )
                and len(center_holes) == 1
                and center_holes[0].hole_type == HoleType.THROUGH
                and center_holes[0].depth is None
            )
        if not matches:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="standard_geometry_mismatch",
                    message="幾何已偏離所宣告的 NEMA17 馬達面標準；請更新幾何或移除該 provenance。",
                )
            )

    @staticmethod
    def _effective_diameter(hole: HoleFeature) -> float:
        return max(
            hole.diameter,
            hole.counterbore_diameter or 0,
            hole.countersink_diameter or 0,
        )

    @staticmethod
    def _plane_coordinates(hole: HoleFeature) -> tuple[float, float]:
        if hole.axis == Axis.Z:
            return hole.x, hole.y
        if hole.axis == Axis.X:
            return hole.y, hole.z
        return hole.x, hole.z

    def _axis_plane(self, doc: CadDocument, axis: Axis) -> tuple[float, float, float, float]:
        dim_x, dim_y, dim_z = self._dimensions(doc.base)
        if axis == Axis.Z:
            return dim_x, dim_y, 0.0, 0.0
        if axis == Axis.X:
            return dim_y, dim_z, 0.0, dim_z / 2
        return dim_x, dim_z, 0.0, dim_z / 2

    @staticmethod
    def _material_depth(doc: CadDocument, hole: HoleFeature) -> float:
        base = doc.base
        if isinstance(base, PlateBase):
            return {Axis.X: base.length, Axis.Y: base.width, Axis.Z: base.thickness}[hole.axis]
        if isinstance(base, EnclosureBase):
            return base.wall_thickness
        if isinstance(base, LBracketBase):
            return base.width if hole.axis == Axis.X else base.thickness
        if isinstance(base, CylinderBase):
            return base.height if hole.axis == Axis.Z else base.diameter
        if isinstance(base, RingBase):
            return base.height if hole.axis == Axis.Z else base.outer_diameter
        return 1.0

    @staticmethod
    def _dimensions(base) -> tuple[float, float, float]:
        if isinstance(base, PlateBase):
            return base.length, base.width, base.thickness
        if isinstance(base, CylinderBase):
            return base.diameter, base.diameter, base.height
        if isinstance(base, RingBase):
            return base.outer_diameter, base.outer_diameter, base.height
        if isinstance(base, LBracketBase):
            return base.width, base.depth, base.vertical_height + base.thickness
        return base.length, base.width, base.height
