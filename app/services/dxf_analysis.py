from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Literal

import ezdxf

from app.models.cad import (
    ArcSegment2D,
    Axis,
    CadDocument,
    ChamferFeature,
    EdgeSelector,
    FilletFeature,
    HoleFeature,
    HoleType,
    LineSegment2D,
    PlannerMetadata,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
    ProfileRevolutionBase,
)
from app.models.dxf import (
    DxfAnalysisResponse,
    DxfCircleHole,
    DxfEdgeTreatment,
    DxfEntityCounts,
    DxfFeatureTreeNode,
    DxfHolePattern,
    DxfProvenance,
    DxfRevolutionAxis,
    DxfSymmetry,
)
from app.services.preview import SvgPreview
from app.services.validator import DesignValidator


class DxfAnalysisError(ValueError):
    pass


_BINARY_SIGNATURE = b"AutoCAD Binary DXF\r\n\x1a\x00"
_REJECTED_SIGNATURES = {
    b"PK\x03\x04": "ZIP archives are not DXF files",
    b"%PDF-": "PDF files are not DXF files",
}
_ANNOTATION_TYPES = {"TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER", "TOLERANCE", "POINT"}
_BLOCKED_TYPES = {"INSERT", "SPLINE", "HATCH", "ELLIPSE", "IMAGE", "XREF"}
_UNIT_SCALES = {1: ("inch", 25.4), 4: ("mm", 1.0), 5: ("cm", 10.0)}


@dataclass(frozen=True)
class _RawLine:
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass(frozen=True)
class _RawArc:
    start: tuple[float, float]
    mid: tuple[float, float]
    end: tuple[float, float]


RawSegment = _RawLine | _RawArc


class DxfFeatureExtractor:
    """Parse the intentionally narrow and review-gated DXF import format."""

    def __init__(
        self,
        *,
        max_bytes: int,
        max_entities: int,
        max_segments: int,
        max_holes: int,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_entities = max_entities
        self.max_segments = max_segments
        self.max_holes = max_holes

    def analyze(
        self,
        data: bytes,
        *,
        thickness_mm: float,
        unit_override: Literal["auto", "mm", "inch", "cm"] = "auto",
        operation_mode: Literal["auto", "extrude", "revolve"] = "auto",
    ) -> DxfAnalysisResponse:
        if not math.isfinite(thickness_mm) or thickness_mm <= 0:
            raise DxfAnalysisError("thickness_mm must be a positive finite number")
        dxf_format = self._sniff(data)
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="promptcad-dxf-", suffix=".dxf", delete=False) as stream:
                stream.write(data)
                path = Path(stream.name)
            return self.analyze_path(
                path,
                thickness_mm=thickness_mm,
                unit_override=unit_override,
                operation_mode=operation_mode,
                source_data=data,
                dxf_format=dxf_format,
            )
        finally:
            if path is not None:
                with suppress(FileNotFoundError):
                    os.unlink(path)

    def analyze_path(
        self,
        path: Path,
        *,
        thickness_mm: float,
        unit_override: Literal["auto", "mm", "inch", "cm"] = "auto",
        operation_mode: Literal["auto", "extrude", "revolve"] = "auto",
        source_data: bytes | None = None,
        dxf_format: Literal["ASCII", "BINARY"] | None = None,
    ) -> DxfAnalysisResponse:
        """Analyze a server-owned temporary DXF path; the caller owns its lifecycle."""
        if not math.isfinite(thickness_mm) or thickness_mm <= 0:
            raise DxfAnalysisError("thickness_mm must be a positive finite number")
        if operation_mode not in {"auto", "extrude", "revolve"}:
            raise DxfAnalysisError("operation_mode must be auto, extrude, or revolve")
        try:
            data = source_data if source_data is not None else path.read_bytes()
        except OSError as exc:
            raise DxfAnalysisError("DXF temporary file cannot be read") from exc
        detected_format = dxf_format or self._sniff(data)
        document = self._read_document(path)
        self._validate_document_structure(document)
        source_unit, scale, insunits = self._unit_scale(document, unit_override)
        counts = DxfEntityCounts()
        raw_segments: list[RawSegment] = []
        raw_holes: list[tuple[float, float, float]] = []
        centerlines: list[tuple[_RawLine, Literal["layer", "linetype"]]] = []

        modelspace = document.modelspace()
        for index, entity in enumerate(modelspace, start=1):
            if index > self.max_entities:
                raise DxfAnalysisError(f"DXF exceeds the {self.max_entities} entity limit")
            entity_type = entity.dxftype().upper()
            if entity_type in _ANNOTATION_TYPES:
                counts.ignored_annotations += 1
                continue
            if entity_type in _BLOCKED_TYPES:
                raise DxfAnalysisError(f"Unsupported DXF entity: {entity_type}")
            if entity_type == "LINE":
                line = self._line(entity)
                centerline_source = self._centerline_source(entity, document)
                if centerline_source is None:
                    raw_segments.append(line)
                else:
                    centerlines.append((line, centerline_source))
                    counts.centerlines += 1
                counts.lines += 1
            elif entity_type == "ARC":
                raw_segments.append(self._arc(entity))
                counts.arcs += 1
            elif entity_type == "CIRCLE":
                raw_holes.append(self._circle(entity))
                counts.circles += 1
            elif entity_type == "LWPOLYLINE":
                raw_segments.extend(self._lwpolyline(entity))
                counts.lwpolylines += 1
            elif entity_type == "POLYLINE":
                raw_segments.extend(self._polyline(entity))
                counts.polylines += 1
            else:
                raise DxfAnalysisError(f"Unsupported DXF entity: {entity_type}")

            if len(raw_segments) + len(centerlines) > self.max_segments:
                raise DxfAnalysisError(f"DXF exceeds the {self.max_segments} segment limit")
            if len(raw_holes) > self.max_holes:
                raise DxfAnalysisError(f"DXF exceeds the {self.max_holes} circular-hole limit")

        if not raw_segments:
            raise DxfAnalysisError("DXF requires one closed outer profile")
        if len(centerlines) > 1:
            raise DxfAnalysisError("DXF revolution inference accepts exactly one centerline")
        revolution_axis = (
            self._revolution_axis(centerlines[0][0], centerlines[0][1], scale)
            if centerlines
            else None
        )
        try:
            profile = ProfileLoop2D(segments=self._closed_chain(raw_segments, scale))
        except DxfAnalysisError:
            if not centerlines:
                raise
            profile = ProfileLoop2D(
                segments=self._closed_chain([*raw_segments, centerlines[0][0]], scale)
            )
        holes = [
            DxfCircleHole(
                id=f"circle-{index:02d}",
                center=self._point(x * scale, y * scale),
                radius_mm=radius * scale,
            )
            for index, (x, y, radius) in enumerate(sorted(raw_holes), start=1)
        ]
        inferred_operation: Literal["extrude", "revolve"] = (
            "revolve" if operation_mode == "revolve" or (operation_mode == "auto" and revolution_axis) else "extrude"
        )
        if inferred_operation == "revolve" and revolution_axis is None:
            raise DxfAnalysisError("Revolution requires one horizontal or vertical CENTER line")
        if inferred_operation == "revolve" and holes:
            raise DxfAnalysisError("The first revolution slice does not support circular holes")
        patterns = self._hole_patterns(holes) if inferred_operation == "extrude" else []
        modeling_profile, edge_treatments = (
            self._global_edge_treatment(profile)
            if inferred_operation == "extrude"
            else (profile, [])
        )
        tree = self._feature_tree(
            modeling_profile,
            holes,
            thickness_mm,
            operation=inferred_operation,
            revolution_axis=revolution_axis,
            patterns=patterns,
            edge_treatments=edge_treatments,
        )
        provenance = DxfProvenance(
            dxf_sha256=hashlib.sha256(data).hexdigest(),
            canonical_geometry_sha256=self._canonical_geometry_sha256(profile, holes),
            dxf_format=detected_format,
            byte_length=len(data),
            dxf_version=str(document.dxfversion),
            parser_version=str(ezdxf.__version__),
            entity_total=len(modelspace),
            insunits=insunits,
            source_unit=source_unit,
            unit_scale_to_mm=scale,
        )
        result = DxfAnalysisResponse(
            provenance=provenance,
            entity_counts=counts,
            outer_profile=profile,
            holes=holes,
            patterns=patterns,
            edge_treatments=edge_treatments,
            inferred_operation=inferred_operation,
            revolution_axis=revolution_axis,
            feature_tree=tree,
            symmetry=self._symmetry(profile, holes),
            convertible=False,
            warnings=[
                "DXF 幾何已轉為可編輯 Feature Tree；輸出 CAD 前必須人工確認。",
                *(
                    ["已依 CENTER 線推論為繞全域 Z 軸 360° 旋轉；請確認剖面與中心線。"]
                    if inferred_operation == "revolve"
                    else []
                ),
            ],
        )
        spec = self.spec_from_feature_tree(result.feature_tree, provenance)
        validation = DesignValidator().validate(spec)
        return result.model_copy(
            update={
                "convertible": validation.valid,
                "proposed_spec": spec if validation.valid else None,
                "validation": validation,
                "preview_svg": SvgPreview().render(spec) if validation.valid else None,
            }
        )

    def spec_from_feature_tree(
        self,
        feature_tree: list[DxfFeatureTreeNode],
        provenance: DxfProvenance,
    ) -> CadDocument:
        ids = [node.id for node in feature_tree]
        if not feature_tree or len(ids) != len(set(ids)):
            raise DxfAnalysisError("Feature Tree must have unique nodes")
        profiles = [node for node in feature_tree if node.operation == "profile_loop"]
        extrusions = [node for node in feature_tree if node.operation == "extrude_profile"]
        revolutions = [node for node in feature_tree if node.operation == "revolve_profile"]
        circles = [node for node in feature_tree if node.operation == "circle_hole"]
        pattern_nodes = [node for node in feature_tree if node.operation == "hole_pattern"]
        fillet_nodes = [node for node in feature_tree if node.operation == "fillet_edges"]
        chamfer_nodes = [node for node in feature_tree if node.operation == "chamfer_edges"]
        if len(profiles) != 1 or len(extrusions) + len(revolutions) != 1:
            raise DxfAnalysisError("Feature Tree requires exactly one profile and one base operation")
        profile = profiles[0]
        if profile.loop is None:
            raise DxfAnalysisError("Profile node requires a closed loop")
        holes: list[HoleFeature] = []
        for circle in circles:
            if circle.parent_id != profile.id or circle.center is None or circle.radius_mm is None:
                raise DxfAnalysisError("Each circular hole must reference the profile loop")
            holes.append(
                HoleFeature(
                    x=circle.center.x,
                    y=circle.center.y,
                    axis=Axis.Z,
                    diameter=circle.radius_mm * 2,
                    hole_type=HoleType.THROUGH,
                )
            )
        for node in pattern_nodes:
            if node.parent_id != profile.id or node.pattern is None:
                raise DxfAnalysisError("Each hole pattern must reference the profile loop")
            holes.extend(self._expanded_pattern_holes(node.pattern))
        if len(holes) > self.max_holes:
            raise DxfAnalysisError(f"Feature Tree exceeds the {self.max_holes} circular-hole limit")
        if len(fillet_nodes) > 1 or len(chamfer_nodes) > 1 or (fillet_nodes and chamfer_nodes):
            raise DxfAnalysisError("Feature Tree accepts one global fillet or chamfer operation")
        for node in [*fillet_nodes, *chamfer_nodes]:
            if node.parent_id != profile.id or node.edge_treatment is None:
                raise DxfAnalysisError("Edge treatment must reference the profile loop")

        if extrusions:
            extrusion = extrusions[0]
            if extrusion.parent_id != profile.id or extrusion.thickness_mm is None:
                raise DxfAnalysisError("Extrusion must reference the profile loop")
            base = ProfileExtrusionBase(outer=profile.loop, thickness=extrusion.thickness_mm)
            schema_version = "1.1"
            name = "dxf-extracted-profile"
            prompt = "由受限 DXF 可編輯 Feature Tree 建立閉合輪廓、孔與陣列拉伸"
            assumptions = ["DXF 僅使用 modelspace 的 2D 幾何。", "拉伸厚度由使用者指定。"]
        else:
            revolution = revolutions[0]
            if revolution.parent_id != profile.id or revolution.revolution_axis is None:
                raise DxfAnalysisError("Revolution must reference the profile loop and CENTER axis")
            if holes:
                raise DxfAnalysisError("The first revolution slice does not support circular holes")
            if fillet_nodes or chamfer_nodes:
                raise DxfAnalysisError("The first revolution slice does not support edge treatments")
            base = ProfileRevolutionBase(
                outer=self._profile_for_revolution(profile.loop, revolution.revolution_axis)
            )
            schema_version = "1.2"
            name = "dxf-revolved-profile"
            prompt = "由受限 DXF 半剖面與 CENTER 線建立 360 度旋轉實體"
            assumptions = [
                "DXF 僅使用 modelspace 的 2D 幾何。",
                "CENTER 線為旋轉軸，剖面繞全域 Z 軸旋轉 360 度。",
            ]
        fillets = [
            FilletFeature(
                radius=node.edge_treatment.size_mm,
                selector=EdgeSelector.VERTICAL,
            )
            for node in fillet_nodes
            if node.edge_treatment is not None
        ]
        chamfers = [
            ChamferFeature(
                distance=node.edge_treatment.size_mm,
                selector=EdgeSelector.VERTICAL,
            )
            for node in chamfer_nodes
            if node.edge_treatment is not None
        ]
        return CadDocument(
            schema_version=schema_version,
            name=name,
            source_prompt=prompt,
            base=base,
            holes=holes,
            fillets=fillets,
            chamfers=chamfers,
            assumptions=assumptions,
            notes=[
                f"DXF SHA-256：{provenance.dxf_sha256}",
                f"原始單位：{provenance.source_unit}",
                "分析版本：1.1",
            ],
            planner=PlannerMetadata(planner="dxf-feature-tree", confidence=1.0, review_required=True),
        )

    @staticmethod
    def _expanded_pattern_holes(pattern: DxfHolePattern) -> list[HoleFeature]:
        centers: list[Point2D]
        if pattern.kind == "linear":
            assert pattern.direction is not None and pattern.spacing_mm is not None
            dx, dy = (pattern.spacing_mm, 0.0) if pattern.direction == "x" else (0.0, pattern.spacing_mm)
            centers = [
                Point2D(x=pattern.seed_center.x + index * dx, y=pattern.seed_center.y + index * dy)
                for index in range(pattern.count)
            ]
        else:
            assert pattern.pattern_center is not None
            assert pattern.pattern_radius_mm is not None
            assert pattern.start_angle_deg is not None
            assert pattern.angular_spacing_deg is not None
            centers = []
            for index in range(pattern.count):
                angle = math.radians(pattern.start_angle_deg + index * pattern.angular_spacing_deg)
                centers.append(
                    Point2D(
                        x=pattern.pattern_center.x + pattern.pattern_radius_mm * math.cos(angle),
                        y=pattern.pattern_center.y + pattern.pattern_radius_mm * math.sin(angle),
                    )
                )
        return [
            HoleFeature(
                x=center.x,
                y=center.y,
                axis=Axis.Z,
                diameter=pattern.hole_radius_mm * 2,
                hole_type=HoleType.THROUGH,
            )
            for center in centers
        ]

    def _sniff(self, data: bytes) -> Literal["ASCII", "BINARY"]:
        if not data:
            raise DxfAnalysisError("DXF file is empty")
        if len(data) > self.max_bytes:
            raise DxfAnalysisError(f"DXF exceeds the {self.max_bytes} byte limit")
        prefix = data[:512]
        for signature, message in _REJECTED_SIGNATURES.items():
            if prefix.startswith(signature):
                raise DxfAnalysisError(message)
        stripped = prefix.lstrip()
        if stripped.startswith((b"<?xml", b"<svg", b"<html", b"<!DOCTYPE")):
            raise DxfAnalysisError("XML files are not DXF files")
        if prefix.startswith(_BINARY_SIGNATURE):
            return "BINARY"
        try:
            text = prefix.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DxfAnalysisError("DXF is neither recognized ASCII nor binary DXF") from exc
        if "SECTION" not in text:
            raise DxfAnalysisError("DXF is not a recognized ASCII DXF document")
        return "ASCII"

    @staticmethod
    def _read_document(path: Path):
        try:
            return ezdxf.readfile(path)
        except (OSError, ezdxf.DXFError, UnicodeError) as exc:
            raise DxfAnalysisError("DXF is invalid, truncated, or unsafe to parse") from exc

    def _validate_document_structure(self, document) -> None:
        if len(document.entitydb) > self.max_entities + 512:
            raise DxfAnalysisError(
                f"DXF exceeds the {self.max_entities} entity budget outside modelspace"
            )
        for block in document.blocks:
            if block.name.upper().startswith(("*MODEL_SPACE", "*PAPER_SPACE")):
                continue
            if len(block):
                raise DxfAnalysisError("Unsupported DXF BLOCKS content")

    @staticmethod
    def _unit_scale(document, override: str) -> tuple[Literal["mm", "inch", "cm"], float, int]:
        if override != "auto":
            scale = {"mm": 1.0, "inch": 25.4, "cm": 10.0}[override]
            return override, scale, int(document.header.get("$INSUNITS", 0))
        insunits = int(document.header.get("$INSUNITS", 0))
        if insunits not in _UNIT_SCALES:
            raise DxfAnalysisError("DXF auto units require INSUNITS set to mm, inch, or cm")
        unit, scale = _UNIT_SCALES[insunits]
        return unit, scale, insunits

    @classmethod
    def _line(cls, entity) -> _RawLine:
        start, end = entity.dxf.start, entity.dxf.end
        cls._assert_2d(start, end)
        return _RawLine((float(start.x), float(start.y)), (float(end.x), float(end.y)))

    @classmethod
    def _arc(cls, entity) -> _RawArc:
        cls._assert_wcs(entity)
        center = entity.dxf.center
        cls._assert_2d(center)
        radius = float(entity.dxf.radius)
        if not math.isfinite(radius) or radius <= 0:
            raise DxfAnalysisError("ARC radius must be positive and finite")
        start_angle = float(entity.dxf.start_angle)
        end_angle = float(entity.dxf.end_angle)
        sweep = (end_angle - start_angle) % 360
        if sweep <= 1e-9:
            raise DxfAnalysisError("ARC must have a non-zero sweep")
        return cls._arc_from_center((float(center.x), float(center.y)), radius, start_angle, sweep)

    @classmethod
    def _circle(cls, entity) -> tuple[float, float, float]:
        cls._assert_wcs(entity)
        center = entity.dxf.center
        cls._assert_2d(center)
        radius = float(entity.dxf.radius)
        if not math.isfinite(radius) or radius <= 0:
            raise DxfAnalysisError("CIRCLE radius must be positive and finite")
        return (float(center.x), float(center.y), radius)

    @staticmethod
    def _centerline_source(entity, document) -> Literal["layer", "linetype"] | None:
        layer = str(entity.dxf.get("layer", "")).upper()
        if "CENTER" in layer:
            return "layer"
        linetype = str(entity.dxf.get("linetype", "")).upper()
        if "CENTER" in linetype:
            return "linetype"
        if linetype in {"", "BYLAYER"}:
            try:
                layer_linetype = str(document.layers.get(layer).dxf.linetype).upper()
            except (KeyError, AttributeError):
                return None
            if "CENTER" in layer_linetype:
                return "linetype"
        return None

    @classmethod
    def _revolution_axis(
        cls,
        line: _RawLine,
        source: Literal["layer", "linetype"],
        scale: float,
    ) -> DxfRevolutionAxis:
        start = cls._point(line.start[0] * scale, line.start[1] * scale)
        end = cls._point(line.end[0] * scale, line.end[1] * scale)
        tolerance = max(1e-7, math.dist(line.start, line.end) * scale * 1e-8)
        if math.dist((start.x, start.y), (end.x, end.y)) <= tolerance:
            raise DxfAnalysisError("CENTER line must have a non-zero length")
        if abs(start.x - end.x) <= tolerance:
            return DxfRevolutionAxis(
                orientation="vertical",
                offset_mm=(start.x + end.x) / 2,
                start=start,
                end=end,
                source=source,
            )
        if abs(start.y - end.y) <= tolerance:
            return DxfRevolutionAxis(
                orientation="horizontal",
                offset_mm=(start.y + end.y) / 2,
                start=start,
                end=end,
                source=source,
            )
        raise DxfAnalysisError("CENTER line must be horizontal or vertical for revolution")

    @classmethod
    def _lwpolyline(cls, entity) -> list[RawSegment]:
        if not entity.closed:
            raise DxfAnalysisError("LWPOLYLINE outer profile must be closed")
        cls._assert_wcs(entity)
        elevation = float(getattr(entity.dxf, "elevation", 0) or 0)
        if not math.isfinite(elevation) or abs(elevation) > 1e-9:
            raise DxfAnalysisError("3D geometry is not supported")
        points = [(float(x), float(y), float(bulge)) for x, y, _start, _end, bulge in entity.get_points("xyseb")]
        return cls._poly_segments(points)

    @classmethod
    def _polyline(cls, entity) -> list[RawSegment]:
        if not entity.is_2d_polyline or not entity.is_closed:
            raise DxfAnalysisError("Only closed 2D POLYLINE outer profiles are supported")
        cls._assert_wcs(entity)
        points = []
        for vertex in entity.vertices:
            location = vertex.dxf.location
            cls._assert_2d(location)
            points.append((float(location.x), float(location.y), float(vertex.dxf.get("bulge", 0))))
        return cls._poly_segments(points)

    @classmethod
    def _poly_segments(cls, points: list[tuple[float, float, float]]) -> list[RawSegment]:
        if len(points) < 3:
            raise DxfAnalysisError("Closed polyline requires at least three vertices")
        segments: list[RawSegment] = []
        for index, (x, y, bulge) in enumerate(points):
            end = points[(index + 1) % len(points)]
            if not all(math.isfinite(value) for value in (x, y, bulge, end[0], end[1])):
                raise DxfAnalysisError("DXF coordinates must be finite")
            if math.dist((x, y), end[:2]) <= 1e-12:
                raise DxfAnalysisError("DXF profile contains a zero-length segment")
            if abs(bulge) <= 1e-12:
                segments.append(_RawLine((x, y), end[:2]))
            else:
                segments.append(cls._arc_from_bulge((x, y), end[:2], bulge))
        return segments

    @staticmethod
    def _assert_2d(*points) -> None:
        for point in points:
            if not all(math.isfinite(float(value)) for value in point):
                raise DxfAnalysisError("DXF coordinates must be finite")
            if abs(float(point.z)) > 1e-9:
                raise DxfAnalysisError("3D geometry is not supported")

    @staticmethod
    def _assert_wcs(entity) -> None:
        extrusion = entity.dxf.get("extrusion", (0, 0, 1))
        try:
            ex, ey, ez = (float(value) for value in extrusion)
        except (TypeError, ValueError) as exc:
            raise DxfAnalysisError("DXF extrusion is invalid") from exc
        if not all(math.isfinite(value) for value in (ex, ey, ez)) or abs(ex) > 1e-9 or abs(ey) > 1e-9 or abs(ez - 1) > 1e-9:
            raise DxfAnalysisError("Only WCS +Z 2D geometry is supported")
        elevation = entity.dxf.get("elevation", 0)
        if hasattr(elevation, "z"):
            values = tuple(float(value) for value in elevation)
            if len(values) != 3 or any(abs(value) > 1e-9 for value in values):
                raise DxfAnalysisError("3D geometry is not supported")
        elif not math.isfinite(float(elevation)) or abs(float(elevation)) > 1e-9:
            raise DxfAnalysisError("3D geometry is not supported")

    @staticmethod
    def _arc_from_center(center: tuple[float, float], radius: float, start_angle: float, sweep: float) -> _RawArc:
        def at(angle: float) -> tuple[float, float]:
            radians = math.radians(angle)
            return center[0] + radius * math.cos(radians), center[1] + radius * math.sin(radians)

        return _RawArc(at(start_angle), at(start_angle + sweep / 2), at(start_angle + sweep))

    @classmethod
    def _arc_from_bulge(cls, start: tuple[float, float], end: tuple[float, float], bulge: float) -> _RawArc:
        chord = math.dist(start, end)
        sweep = math.degrees(4 * math.atan(bulge))
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        left = (-(end[1] - start[1]) / chord, (end[0] - start[0]) / chord)
        offset = chord * (1 - bulge * bulge) / (4 * bulge)
        center = (midpoint[0] + left[0] * offset, midpoint[1] + left[1] * offset)
        radius = chord * (1 + bulge * bulge) / (4 * abs(bulge))
        start_angle = math.degrees(math.atan2(start[1] - center[1], start[0] - center[0]))
        return cls._arc_from_center(center, radius, start_angle, sweep)

    def _closed_chain(self, segments: list[RawSegment], scale: float):
        if len(segments) > self.max_segments:
            raise DxfAnalysisError(f"DXF exceeds the {self.max_segments} segment limit")
        pending = list(segments)
        ordered = [pending.pop(0)]
        tolerance = max(1e-7, 1e-8 / scale)
        while pending:
            endpoint = self._end(ordered[-1])
            matching_index = next(
                (
                    index
                    for index, segment in enumerate(pending)
                    if self._near(endpoint, self._start(segment), tolerance)
                    or self._near(endpoint, self._end(segment), tolerance)
                ),
                None,
            )
            if matching_index is None:
                raise DxfAnalysisError("DXF outer profile is disconnected or has multiple loops")
            next_segment = pending.pop(matching_index)
            if self._near(endpoint, self._end(next_segment), tolerance):
                next_segment = self._reversed(next_segment)
            ordered.append(next_segment)
        if not self._near(self._end(ordered[-1]), self._start(ordered[0]), tolerance):
            raise DxfAnalysisError("DXF outer profile must be closed")
        return [self._to_model(segment, scale) for segment in ordered]

    @staticmethod
    def _start(segment: RawSegment) -> tuple[float, float]:
        return segment.start

    @staticmethod
    def _end(segment: RawSegment) -> tuple[float, float]:
        return segment.end

    @staticmethod
    def _near(left: tuple[float, float], right: tuple[float, float], tolerance: float) -> bool:
        return math.dist(left, right) <= tolerance

    @staticmethod
    def _reversed(segment: RawSegment) -> RawSegment:
        if isinstance(segment, _RawLine):
            return _RawLine(segment.end, segment.start)
        return _RawArc(segment.end, segment.mid, segment.start)

    def _to_model(self, segment: RawSegment, scale: float):
        if isinstance(segment, _RawLine):
            return LineSegment2D(start=self._point(*self._scaled(segment.start, scale)), end=self._point(*self._scaled(segment.end, scale)))
        return ArcSegment2D(
            start=self._point(*self._scaled(segment.start, scale)),
            mid=self._point(*self._scaled(segment.mid, scale)),
            end=self._point(*self._scaled(segment.end, scale)),
        )

    @staticmethod
    def _scaled(point: tuple[float, float], scale: float) -> tuple[float, float]:
        return point[0] * scale, point[1] * scale

    @staticmethod
    def _point(x: float, y: float) -> Point2D:
        if not math.isfinite(x) or not math.isfinite(y):
            raise DxfAnalysisError("DXF coordinates must be finite")
        return Point2D(x=x, y=y)

    @classmethod
    def _profile_for_revolution(
        cls,
        profile: ProfileLoop2D,
        axis: DxfRevolutionAxis,
    ) -> ProfileLoop2D:
        points = []
        for segment in profile.segments:
            points.extend([segment.start, segment.end])
            if isinstance(segment, ArcSegment2D):
                points.append(segment.mid)
        if axis.orientation == "vertical":
            radial_values = [point.x - axis.offset_mm for point in points]
            axial_values = [point.y for point in points]
        else:
            radial_values = [point.y - axis.offset_mm for point in points]
            axial_values = [point.x for point in points]
        span = max(max(axial_values) - min(axial_values), max(radial_values) - min(radial_values), 1.0)
        tolerance = max(1e-7, span * 1e-7)
        if min(abs(value) for value in radial_values) > tolerance:
            raise DxfAnalysisError("Revolution profile must touch the CENTER axis")
        has_positive = any(value > tolerance for value in radial_values)
        has_negative = any(value < -tolerance for value in radial_values)
        if has_positive and has_negative:
            raise DxfAnalysisError("Revolution profile must stay on one side of the CENTER axis")
        sign = -1.0 if has_negative else 1.0
        axial_min = min(axial_values)

        def transformed(point: Point2D) -> Point2D:
            if axis.orientation == "vertical":
                radius, axial = sign * (point.x - axis.offset_mm), point.y - axial_min
            else:
                radius, axial = sign * (point.y - axis.offset_mm), point.x - axial_min
            if radius < -tolerance:
                raise DxfAnalysisError("Revolution profile crosses the CENTER axis")
            return Point2D(x=max(0.0, radius), y=axial)

        transformed_segments = []
        for segment in profile.segments:
            if isinstance(segment, LineSegment2D):
                transformed_segments.append(
                    LineSegment2D(start=transformed(segment.start), end=transformed(segment.end))
                )
            else:
                transformed_segments.append(
                    ArcSegment2D(
                        start=transformed(segment.start),
                        mid=transformed(segment.mid),
                        end=transformed(segment.end),
                    )
                )
        return ProfileLoop2D(segments=transformed_segments)

    @classmethod
    def _global_edge_treatment(
        cls,
        profile: ProfileLoop2D,
    ) -> tuple[ProfileLoop2D, list[DxfEdgeTreatment]]:
        """Recognize only uniform axis-aligned rounded/chamfered rectangles."""
        if len(profile.segments) != 8:
            return profile, []
        points = [point for segment in profile.segments for point in (segment.start, segment.end)]
        min_x, max_x = min(point.x for point in points), max(point.x for point in points)
        min_y, max_y = min(point.y for point in points), max(point.y for point in points)
        span = max(max_x - min_x, max_y - min_y, 1.0)
        tolerance = max(1e-6, span * 1e-6)
        if max_x - min_x <= tolerance or max_y - min_y <= tolerance:
            return profile, []

        def bbox_edges(point: Point2D) -> set[str]:
            result = set()
            if abs(point.x - min_x) <= tolerance:
                result.add("left")
            if abs(point.x - max_x) <= tolerance:
                result.add("right")
            if abs(point.y - min_y) <= tolerance:
                result.add("bottom")
            if abs(point.y - max_y) <= tolerance:
                result.add("top")
            return result

        arcs = [
            (index, segment)
            for index, segment in enumerate(profile.segments)
            if isinstance(segment, ArcSegment2D)
        ]
        lines = [
            (index, segment)
            for index, segment in enumerate(profile.segments)
            if isinstance(segment, LineSegment2D)
        ]
        treatment: DxfEdgeTreatment | None = None
        if len(arcs) == 4 and len(lines) == 4:
            if not all(
                abs(line.start.x - line.end.x) <= tolerance
                or abs(line.start.y - line.end.y) <= tolerance
                for _index, line in lines
            ):
                return profile, []
            radii = []
            for _index, arc in arcs:
                if not cls._joins_adjacent_bbox_edges(arc.start, arc.end, bbox_edges):
                    return profile, []
                circle = cls._circumcircle(arc.start, arc.mid, arc.end, tolerance)
                if circle is None:
                    return profile, []
                center_x, center_y, radius = circle
                edge_pair = bbox_edges(arc.start) | bbox_edges(arc.end)
                expected_x = (
                    min_x + radius
                    if "left" in edge_pair
                    else max_x - radius
                )
                expected_y = (
                    min_y + radius
                    if "bottom" in edge_pair
                    else max_y - radius
                )
                if (
                    abs(center_x - expected_x) > tolerance
                    or abs(center_y - expected_y) > tolerance
                ):
                    return profile, []
                corner_x = min_x if "left" in edge_pair else max_x
                corner_y = min_y if "bottom" in edge_pair else max_y
                if not (
                    min(corner_x, expected_x) - tolerance
                    <= arc.mid.x
                    <= max(corner_x, expected_x) + tolerance
                    and min(corner_y, expected_y) - tolerance
                    <= arc.mid.y
                    <= max(corner_y, expected_y) + tolerance
                ):
                    return profile, []
                radii.append(radius)
            size = sum(radii) / len(radii)
            if any(abs(radius - size) > tolerance for radius in radii):
                return profile, []
            treatment = DxfEdgeTreatment(
                kind="fillet",
                segment_indices=[index for index, _arc in arcs],
                size_mm=size,
            )
        elif len(lines) == 8:
            diagonal = []
            axis_aligned = []
            for index, line in lines:
                dx = abs(line.end.x - line.start.x)
                dy = abs(line.end.y - line.start.y)
                if dx <= tolerance or dy <= tolerance:
                    axis_aligned.append((index, line))
                else:
                    diagonal.append((index, line, dx, dy))
            if len(axis_aligned) != 4 or len(diagonal) != 4:
                return profile, []
            distances = []
            for _index, line, dx, dy in diagonal:
                if abs(dx - dy) > tolerance:
                    return profile, []
                if not cls._joins_adjacent_bbox_edges(line.start, line.end, bbox_edges):
                    return profile, []
                distances.append((dx + dy) / 2)
            size = sum(distances) / len(distances)
            if any(abs(distance - size) > tolerance for distance in distances):
                return profile, []
            treatment = DxfEdgeTreatment(
                kind="chamfer",
                segment_indices=[index for index, _line, _dx, _dy in diagonal],
                size_mm=size,
            )
        if treatment is None:
            return profile, []
        corners = [
            Point2D(x=min_x, y=min_y),
            Point2D(x=max_x, y=min_y),
            Point2D(x=max_x, y=max_y),
            Point2D(x=min_x, y=max_y),
        ]
        sharp_profile = ProfileLoop2D(
            segments=[
                LineSegment2D(start=corner, end=corners[(index + 1) % 4])
                for index, corner in enumerate(corners)
            ]
        )
        return sharp_profile, [treatment]

    @staticmethod
    def _joins_adjacent_bbox_edges(start, end, edge_lookup) -> bool:
        start_edges = edge_lookup(start)
        end_edges = edge_lookup(end)
        if len(start_edges) != 1 or len(end_edges) != 1 or start_edges == end_edges:
            return False
        opposite = ({"left", "right"}, {"top", "bottom"})
        return not any(start_edges | end_edges == pair for pair in opposite)

    @staticmethod
    def _circumcircle(
        start: Point2D,
        mid: Point2D,
        end: Point2D,
        tolerance: float,
    ) -> tuple[float, float, float] | None:
        determinant = 2 * (
            start.x * (mid.y - end.y)
            + mid.x * (end.y - start.y)
            + end.x * (start.y - mid.y)
        )
        if abs(determinant) <= tolerance:
            return None
        start_sq = start.x * start.x + start.y * start.y
        mid_sq = mid.x * mid.x + mid.y * mid.y
        end_sq = end.x * end.x + end.y * end.y
        center_x = (
            start_sq * (mid.y - end.y)
            + mid_sq * (end.y - start.y)
            + end_sq * (start.y - mid.y)
        ) / determinant
        center_y = (
            start_sq * (end.x - mid.x)
            + mid_sq * (start.x - end.x)
            + end_sq * (mid.x - start.x)
        ) / determinant
        radius = math.dist((center_x, center_y), (start.x, start.y))
        return center_x, center_y, radius

    @classmethod
    def _hole_patterns(cls, holes: list[DxfCircleHole]) -> list[DxfHolePattern]:
        patterns: list[DxfHolePattern] = []
        unassigned = list(holes)
        pattern_index = 1
        while len(unassigned) >= 3:
            seed = unassigned[0]
            radius_tolerance = max(1e-7, seed.radius_mm * 1e-6)
            group = [hole for hole in unassigned if abs(hole.radius_mm - seed.radius_mm) <= radius_tolerance]
            if len(group) < 3:
                unassigned.remove(seed)
                continue
            pattern = (
                cls._circular_pattern(group, pattern_index)
                or cls._linear_pattern(group, pattern_index)
                or cls._best_circular_subpattern(group, pattern_index)
                or cls._best_linear_subpattern(group, pattern_index)
            )
            if pattern is None:
                unassigned.remove(seed)
                continue
            patterns.append(pattern)
            member_ids = set(pattern.member_ids)
            unassigned = [hole for hole in unassigned if hole.id not in member_ids]
            pattern_index += 1
        return patterns

    @classmethod
    def _best_linear_subpattern(
        cls,
        holes: list[DxfCircleHole],
        pattern_index: int,
    ) -> DxfHolePattern | None:
        span = max(
            max(hole.center.x for hole in holes) - min(hole.center.x for hole in holes),
            max(hole.center.y for hole in holes) - min(hole.center.y for hole in holes),
            1.0,
        )
        tolerance = max(1e-6, span * 1e-6)
        best: list[DxfCircleHole] = []
        for direction in ("x", "y"):
            fixed = (
                (lambda hole: hole.center.y)
                if direction == "x"
                else (lambda hole: hole.center.x)
            )
            varying = (
                (lambda hole: hole.center.x)
                if direction == "x"
                else (lambda hole: hole.center.y)
            )
            groups: list[list[DxfCircleHole]] = []
            for hole in sorted(holes, key=fixed):
                target = next(
                    (
                        group
                        for group in groups
                        if abs(fixed(group[0]) - fixed(hole)) <= tolerance
                    ),
                    None,
                )
                if target is None:
                    groups.append([hole])
                else:
                    target.append(hole)
            for group in groups:
                ordered = sorted(group, key=varying)
                for left_index in range(len(ordered) - 2):
                    for right_index in range(left_index + 1, len(ordered) - 1):
                        spacing = varying(ordered[right_index]) - varying(ordered[left_index])
                        if spacing <= tolerance:
                            continue
                        candidate = [ordered[left_index], ordered[right_index]]
                        expected = varying(ordered[right_index]) + spacing
                        for hole in ordered[right_index + 1 :]:
                            value = varying(hole)
                            if abs(value - expected) <= tolerance:
                                candidate.append(hole)
                                expected += spacing
                        if len(candidate) >= 3 and len(candidate) > len(best):
                            best = candidate
        return cls._linear_pattern(best, pattern_index) if len(best) >= 3 else None

    @classmethod
    def _best_circular_subpattern(
        cls,
        holes: list[DxfCircleHole],
        pattern_index: int,
    ) -> DxfHolePattern | None:
        if len(holes) < 4:
            return None
        span = max(
            max(hole.center.x for hole in holes) - min(hole.center.x for hole in holes),
            max(hole.center.y for hole in holes) - min(hole.center.y for hole in holes),
            1.0,
        )
        tolerance = max(1e-6, span * 1e-6)
        best: DxfHolePattern | None = None
        seen: set[tuple[str, ...]] = set()
        for first, second, third in combinations(holes, 3):
            circle = cls._circumcircle(
                first.center,
                second.center,
                third.center,
                tolerance,
            )
            if circle is None:
                continue
            center_x, center_y, radius = circle
            candidate = [
                hole
                for hole in holes
                if abs(
                    math.dist((hole.center.x, hole.center.y), (center_x, center_y))
                    - radius
                )
                <= tolerance
            ]
            member_key = tuple(sorted(hole.id for hole in candidate))
            if len(candidate) < 3 or member_key in seen:
                continue
            seen.add(member_key)
            pattern = cls._circular_pattern(candidate, pattern_index)
            if pattern is not None and (best is None or pattern.count > best.count):
                best = pattern
        return best

    @staticmethod
    def _linear_pattern(holes: list[DxfCircleHole], pattern_index: int) -> DxfHolePattern | None:
        span = max(
            max(hole.center.x for hole in holes) - min(hole.center.x for hole in holes),
            max(hole.center.y for hole in holes) - min(hole.center.y for hole in holes),
            1.0,
        )
        tolerance = max(1e-6, span * 1e-6)
        x_spread = max(hole.center.x for hole in holes) - min(hole.center.x for hole in holes)
        y_spread = max(hole.center.y for hole in holes) - min(hole.center.y for hole in holes)
        if y_spread <= tolerance and x_spread > tolerance:
            direction: Literal["x", "y"] = "x"
            ordered = sorted(holes, key=lambda hole: hole.center.x)
            coordinates = [hole.center.x for hole in ordered]
        elif x_spread <= tolerance and y_spread > tolerance:
            direction = "y"
            ordered = sorted(holes, key=lambda hole: hole.center.y)
            coordinates = [hole.center.y for hole in ordered]
        else:
            return None
        spacings = [
            right - left
            for left, right in zip(coordinates, coordinates[1:], strict=False)
        ]
        spacing = sum(spacings) / len(spacings)
        if spacing <= tolerance or any(abs(value - spacing) > tolerance for value in spacings):
            return None
        return DxfHolePattern(
            id=f"pattern-{pattern_index:02d}",
            kind="linear",
            member_ids=[hole.id for hole in ordered],
            hole_radius_mm=ordered[0].radius_mm,
            count=len(ordered),
            seed_center=ordered[0].center,
            direction=direction,
            spacing_mm=spacing,
        )

    @staticmethod
    def _circular_pattern(holes: list[DxfCircleHole], pattern_index: int) -> DxfHolePattern | None:
        center = Point2D(
            x=sum(hole.center.x for hole in holes) / len(holes),
            y=sum(hole.center.y for hole in holes) / len(holes),
        )
        radii = [math.dist((hole.center.x, hole.center.y), (center.x, center.y)) for hole in holes]
        pattern_radius = sum(radii) / len(radii)
        tolerance = max(1e-6, pattern_radius * 1e-6)
        if pattern_radius <= tolerance or any(abs(radius - pattern_radius) > tolerance for radius in radii):
            return None
        ordered = sorted(
            holes,
            key=lambda hole: math.atan2(hole.center.y - center.y, hole.center.x - center.x),
        )
        angles = [
            math.degrees(math.atan2(hole.center.y - center.y, hole.center.x - center.x)) % 360
            for hole in ordered
        ]
        gaps = [
            (angles[(index + 1) % len(angles)] - angle) % 360
            for index, angle in enumerate(angles)
        ]
        angular_spacing = 360 / len(holes)
        angular_tolerance = max(1e-5, angular_spacing * 1e-5)
        if any(abs(gap - angular_spacing) > angular_tolerance for gap in gaps):
            return None
        return DxfHolePattern(
            id=f"pattern-{pattern_index:02d}",
            kind="circular",
            member_ids=[hole.id for hole in ordered],
            hole_radius_mm=ordered[0].radius_mm,
            count=len(ordered),
            seed_center=ordered[0].center,
            pattern_center=center,
            pattern_radius_mm=pattern_radius,
            start_angle_deg=angles[0],
            angular_spacing_deg=angular_spacing,
        )

    @staticmethod
    def _feature_tree(
        profile: ProfileLoop2D,
        holes: list[DxfCircleHole],
        thickness_mm: float,
        *,
        operation: Literal["extrude", "revolve"],
        revolution_axis: DxfRevolutionAxis | None,
        patterns: list[DxfHolePattern],
        edge_treatments: list[DxfEdgeTreatment],
    ) -> list[DxfFeatureTreeNode]:
        nodes: list[DxfFeatureTreeNode] = [
            DxfFeatureTreeNode(id="profile-01", operation="profile_loop", loop=profile),
        ]
        if operation == "extrude":
            nodes.append(
                DxfFeatureTreeNode(
                    id="extrude-01",
                    operation="extrude_profile",
                    parent_id="profile-01",
                    thickness_mm=thickness_mm,
                )
            )
        else:
            if revolution_axis is None:
                raise DxfAnalysisError("Revolution Feature Tree requires a CENTER axis")
            nodes.append(
                DxfFeatureTreeNode(
                    id="revolve-01",
                    operation="revolve_profile",
                    parent_id="profile-01",
                    revolution_axis=revolution_axis,
                )
            )
        patterned_ids = {member_id for pattern in patterns for member_id in pattern.member_ids}
        nodes.extend(
            DxfFeatureTreeNode(id=f"sketch-{hole.id}", operation="circle_hole", parent_id="profile-01", center=hole.center, radius_mm=hole.radius_mm)
            for hole in holes
            if hole.id not in patterned_ids
        )
        nodes.extend(
            DxfFeatureTreeNode(
                id=pattern.id,
                operation="hole_pattern",
                parent_id="profile-01",
                pattern=pattern,
            )
            for pattern in patterns
        )
        nodes.extend(
            DxfFeatureTreeNode(
                id=f"{treatment.kind}-01",
                operation="fillet_edges" if treatment.kind == "fillet" else "chamfer_edges",
                parent_id="profile-01",
                edge_treatment=treatment,
            )
            for treatment in edge_treatments
        )
        return nodes

    @staticmethod
    def _canonical_geometry_sha256(profile: ProfileLoop2D, holes: list[DxfCircleHole]) -> str:
        geometry = {
            "outer": profile.model_dump(mode="json"),
            "holes": [hole.model_dump(mode="json") for hole in holes],
        }
        encoded = json.dumps(geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _symmetry(profile: ProfileLoop2D, holes: list[DxfCircleHole]) -> DxfSymmetry:
        points = []
        for segment in profile.segments:
            points.extend([segment.start, segment.end])
            if isinstance(segment, ArcSegment2D):
                points.append(segment.mid)
        xs, ys = [point.x for point in points], [point.y for point in points]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        tolerance = max(1e-6, max(max(xs) - min(xs), max(ys) - min(ys)) * 1e-6)
        geometry = [(point.x, point.y) for point in points] + [(hole.center.x, hole.center.y) for hole in holes]
        def mirrored(axis: str) -> bool:
            targets = {(round(x / tolerance), round(y / tolerance)) for x, y in geometry}
            if axis == "vertical":
                return all((round((2 * cx - x) / tolerance), round(y / tolerance)) in targets for x, y in geometry)
            if axis == "horizontal":
                return all((round(x / tolerance), round((2 * cy - y) / tolerance)) in targets for x, y in geometry)
            return all((round((2 * cx - x) / tolerance), round((2 * cy - y) / tolerance)) in targets for x, y in geometry)
        return DxfSymmetry(axes=[axis for axis in ("horizontal", "vertical", "rotational_180") if mirrored(axis)], tolerance_mm=tolerance)
