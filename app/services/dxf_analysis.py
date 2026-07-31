from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import ezdxf

from app.models.cad import (
    ArcSegment2D,
    Axis,
    CadDocument,
    HoleFeature,
    HoleType,
    LineSegment2D,
    PlannerMetadata,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
)
from app.models.dxf import (
    DxfAnalysisResponse,
    DxfCircleHole,
    DxfEntityCounts,
    DxfFeatureTreeNode,
    DxfProvenance,
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
        source_data: bytes | None = None,
        dxf_format: Literal["ASCII", "BINARY"] | None = None,
    ) -> DxfAnalysisResponse:
        """Analyze a server-owned temporary DXF path; the caller owns its lifecycle."""
        if not math.isfinite(thickness_mm) or thickness_mm <= 0:
            raise DxfAnalysisError("thickness_mm must be a positive finite number")
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
                raw_segments.append(self._line(entity))
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

            if len(raw_segments) > self.max_segments:
                raise DxfAnalysisError(f"DXF exceeds the {self.max_segments} segment limit")
            if len(raw_holes) > self.max_holes:
                raise DxfAnalysisError(f"DXF exceeds the {self.max_holes} circular-hole limit")

        if not raw_segments:
            raise DxfAnalysisError("DXF requires one closed outer profile")
        profile = ProfileLoop2D(segments=self._closed_chain(raw_segments, scale))
        holes = [
            DxfCircleHole(
                id=f"circle-{index:02d}",
                center=self._point(x * scale, y * scale),
                radius_mm=radius * scale,
            )
            for index, (x, y, radius) in enumerate(sorted(raw_holes), start=1)
        ]
        tree = self._feature_tree(profile, holes, thickness_mm)
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
            feature_tree=tree,
            symmetry=self._symmetry(profile, holes),
            convertible=False,
            warnings=["DXF 幾何已轉為可編輯 Feature Tree；輸出 CAD 前必須人工確認。"],
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
        circles = [node for node in feature_tree if node.operation == "circle_hole"]
        if len(profiles) != 1 or len(extrusions) != 1:
            raise DxfAnalysisError("Feature Tree requires exactly one profile and one extrusion")
        profile, extrusion = profiles[0], extrusions[0]
        if extrusion.parent_id != profile.id or profile.loop is None or extrusion.thickness_mm is None:
            raise DxfAnalysisError("Extrusion must reference the profile loop")
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
        return CadDocument(
            schema_version="1.1",
            name="dxf-extracted-profile",
            source_prompt="由受限 DXF 可編輯 Feature Tree 建立閉合輪廓與圓孔拉伸",
            base=ProfileExtrusionBase(outer=profile.loop, thickness=extrusion.thickness_mm),
            holes=holes,
            assumptions=["DXF 僅使用 modelspace 的 2D 幾何。", "拉伸厚度由使用者指定。"],
            notes=[
                f"DXF SHA-256：{provenance.dxf_sha256}",
                f"原始單位：{provenance.source_unit}",
                "分析版本：1.0",
            ],
            planner=PlannerMetadata(planner="dxf-feature-tree", confidence=1.0, review_required=True),
        )

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

    @staticmethod
    def _feature_tree(profile: ProfileLoop2D, holes: list[DxfCircleHole], thickness_mm: float) -> list[DxfFeatureTreeNode]:
        nodes: list[DxfFeatureTreeNode] = [
            DxfFeatureTreeNode(id="profile-01", operation="profile_loop", loop=profile),
            DxfFeatureTreeNode(id="extrude-01", operation="extrude_profile", parent_id="profile-01", thickness_mm=thickness_mm),
        ]
        nodes.extend(
            DxfFeatureTreeNode(id=f"sketch-{hole.id}", operation="circle_hole", parent_id="profile-01", center=hole.center, radius_mm=hole.radius_mm)
            for hole in holes
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
