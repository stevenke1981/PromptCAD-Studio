from __future__ import annotations

import hashlib
import io
import math
import warnings
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models.cad import (
    Axis,
    CadDocument,
    HoleFeature,
    HoleType,
    PlannerMetadata,
    PlateBase,
)
from app.models.image import (
    DetectedCircle,
    DetectedOuterProfile,
    FeatureTreeNode,
    ImageAnalysisResponse,
    ImageCalibration,
    MetricPoint,
    PixelPoint,
)
from app.services.preview import SvgPreview
from app.services.validator import DesignValidator


class ImageAnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class _DecodedImage:
    grayscale: np.ndarray
    image_format: str
    width: int
    height: int


@dataclass(frozen=True)
class _ContourSelection:
    contours: tuple[np.ndarray, ...]
    hierarchy: np.ndarray
    outer_index: int


class ImageFeatureExtractor:
    allowed_formats = {"PNG", "JPEG"}

    def __init__(
        self,
        *,
        max_bytes: int,
        max_pixels: int,
        max_dimension: int,
    ):
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.max_dimension = max_dimension
        self.validator = DesignValidator()
        self.preview = SvgPreview()

    def analyze(
        self,
        data: bytes,
        *,
        known_length_mm: float,
        thickness_mm: float,
    ) -> ImageAnalysisResponse:
        if not math.isfinite(known_length_mm) or known_length_mm <= 0:
            raise ImageAnalysisError("known_length_mm must be a positive finite number")
        if not math.isfinite(thickness_mm) or thickness_mm <= 0:
            raise ImageAnalysisError("thickness_mm must be a positive finite number")

        decoded = self._decode(data)
        image_sha256 = hashlib.sha256(data).hexdigest()
        selected = self._select_contours(decoded.grayscale)
        outer = selected.contours[selected.outer_index]
        outer_area = float(cv2.contourArea(outer))
        perimeter = float(cv2.arcLength(outer, True))
        if outer_area <= 0 or perimeter <= 0:
            raise ImageAnalysisError("No usable outer profile was found")

        rect = cv2.minAreaRect(outer)
        center = np.asarray(rect[0], dtype=np.float64)
        box = cv2.boxPoints(rect).astype(np.float64)
        long_px, short_px, unit_x, unit_y = self._rectangle_axes(box)
        if long_px < 20 or short_px < 20:
            raise ImageAnalysisError("Detected profile is too small for reliable calibration")

        mm_per_pixel = known_length_mm / long_px
        width_mm = short_px * mm_per_pixel
        if width_mm <= 0 or thickness_mm >= min(known_length_mm, width_mm):
            raise ImageAnalysisError("Thickness must be smaller than the extracted plate dimensions")

        rectangularity = min(1.0, outer_area / max(long_px * short_px, 1.0))
        approx = cv2.approxPolyDP(outer, 0.02 * perimeter, True)
        rectangular_geometry = self._quadrilateral_is_rectangular(approx)
        convertible = (
            len(approx) == 4
            and rectangularity >= 0.90
            and rectangular_geometry
        )
        profile_confidence = min(
            1.0,
            max(0.0, rectangularity * (1.0 if len(approx) == 4 else 0.65)),
        )
        rotation_deg = math.degrees(math.atan2(float(unit_x[1]), float(unit_x[0])))

        a = center - unit_x * long_px / 2
        b = center + unit_x * long_px / 2
        calibration = ImageCalibration(
            known_distance_mm=known_length_mm,
            point_a_px=PixelPoint(x=max(0.0, float(a[0])), y=max(0.0, float(a[1]))),
            point_b_px=PixelPoint(x=max(0.0, float(b[0])), y=max(0.0, float(b[1]))),
            pixel_distance=long_px,
            mm_per_pixel=mm_per_pixel,
        )

        circles = self._circles(
            selected,
            outer_area=outer_area,
            center=center,
            unit_x=unit_x,
            unit_y=unit_y,
            mm_per_pixel=mm_per_pixel,
            max_diameter_px=short_px * 0.8,
        )
        outer_profile = DetectedOuterProfile(
            shape="rectangle" if convertible else "unsupported",
            length_mm=known_length_mm if convertible else None,
            width_mm=width_mm if convertible else None,
            rotation_deg=rotation_deg,
            rectangularity=rectangularity,
            confidence=profile_confidence,
        )

        warnings_out = [
            "比例由外框最長邊校準；不使用圖片 DPI 或 EXIF 尺寸。",
            "厚度由使用者指定，無法從單張俯視圖可靠推定。",
            "影像結果預設需要人工確認；透視、遮擋或反光照片不應直接製造。",
        ]
        proposed_spec = None
        validation = None
        feature_tree: list[FeatureTreeNode] = []

        if convertible:
            feature_tree = self._feature_tree(
                length_mm=known_length_mm,
                width_mm=width_mm,
                thickness_mm=thickness_mm,
                circles=circles,
                profile_confidence=profile_confidence,
            )
            proposed_spec = self.spec_from_feature_tree(
                feature_tree,
                image_sha256=image_sha256,
                extra_notes=[f"原始格式：{decoded.image_format}"],
            )
            validation = self.validator.validate(proposed_spec)
            if not validation.valid:
                warnings_out.append("擷取幾何未通過 CAD 驗證，請修正 Feature Tree 後再輸出。")
        else:
            warnings_out.append(
                "外框不是高信心矩形；已停止轉換 CAD，避免將任意輪廓錯當矩形板。"
            )

        return ImageAnalysisResponse(
            image_sha256=image_sha256,
            image_format=decoded.image_format,
            image_width_px=decoded.width,
            image_height_px=decoded.height,
            calibration=calibration,
            outer_profile=outer_profile,
            circles=circles,
            feature_tree=feature_tree,
            convertible=proposed_spec is not None,
            warnings=warnings_out,
            proposed_spec=proposed_spec,
            validation=validation,
            preview_svg=self.preview.render(proposed_spec) if proposed_spec else None,
        )

    def spec_from_feature_tree(
        self,
        feature_tree: list[FeatureTreeNode],
        *,
        image_sha256: str,
        extra_notes: list[str] | None = None,
    ) -> CadDocument:
        if not feature_tree:
            raise ImageAnalysisError("Feature Tree is empty")
        ids = [node.id for node in feature_tree]
        if len(ids) != len(set(ids)):
            raise ImageAnalysisError("Feature Tree node IDs must be unique")
        for node in feature_tree:
            if not all(math.isfinite(value) for value in node.parameters.values()):
                raise ImageAnalysisError(f"Feature Tree node {node.id} has non-finite parameters")

        profiles = [node for node in feature_tree if node.operation == "sketch_rectangle"]
        extrusions = [node for node in feature_tree if node.operation == "extrude"]
        circle_nodes = [node for node in feature_tree if node.operation == "sketch_circle"]
        cut_nodes = [node for node in feature_tree if node.operation == "cut_through"]
        if len(profiles) != 1 or len(extrusions) != 1:
            raise ImageAnalysisError(
                "Feature Tree requires exactly one rectangle profile and one extrusion"
            )
        profile = profiles[0]
        extrusion = extrusions[0]
        self._require_parameters(profile, {"length_mm", "width_mm"})
        self._require_parameters(extrusion, {"distance_mm"})
        if extrusion.parent_id != profile.id:
            raise ImageAnalysisError("Extrusion must reference the rectangle profile")

        cuts_by_parent = {node.parent_id: node for node in cut_nodes}
        if len(cuts_by_parent) != len(cut_nodes):
            raise ImageAnalysisError("Each cut must reference a unique circle sketch")
        holes = []
        for circle in circle_nodes:
            self._require_parameters(circle, {"x_mm", "y_mm", "diameter_mm"})
            cut = cuts_by_parent.get(circle.id)
            if circle.parent_id != profile.id or cut is None:
                raise ImageAnalysisError("Each circle sketch requires a matching through cut")
            self._require_parameters(cut, {"depth_mm"})
            if abs(cut.parameters["depth_mm"] - extrusion.parameters["distance_mm"]) > 1e-6:
                raise ImageAnalysisError("Through-cut depth must match the extrusion thickness")
            holes.append(
                HoleFeature(
                    x=circle.parameters["x_mm"],
                    y=circle.parameters["y_mm"],
                    axis=Axis.Z,
                    diameter=circle.parameters["diameter_mm"],
                    hole_type=HoleType.THROUGH,
                )
            )
        if len(cut_nodes) != len(circle_nodes):
            raise ImageAnalysisError("Feature Tree contains an unreferenced through cut")

        confidence = min(node.confidence for node in feature_tree)
        return CadDocument(
            name="image-extracted-plate",
            source_prompt="由可編輯影像 Feature Tree 建立矩形板與圓孔",
            base=PlateBase(
                length=profile.parameters["length_mm"],
                width=profile.parameters["width_mm"],
                thickness=extrusion.parameters["distance_mm"],
            ),
            holes=holes,
            assumptions=[
                "比例由外框最長邊校準；不使用圖片 DPI 或 EXIF 尺寸。",
                "厚度由使用者指定，無法從單張俯視圖可靠推定。",
                "影像結果需要人工確認；透視、遮擋或反光照片不應直接製造。",
            ],
            notes=[
                f"影像 SHA-256：{image_sha256}",
                "分析版本：1.0",
                *(extra_notes or []),
            ],
            planner=PlannerMetadata(
                planner="image-feature-tree",
                confidence=confidence,
                review_required=True,
            ),
        )

    @staticmethod
    def _require_parameters(node: FeatureTreeNode, expected: set[str]) -> None:
        actual = set(node.parameters)
        if actual != expected:
            raise ImageAnalysisError(
                f"Feature Tree node {node.id} requires parameters: {', '.join(sorted(expected))}"
            )

    def _decode(self, data: bytes) -> _DecodedImage:
        if not data:
            raise ImageAnalysisError("Image file is empty")
        if len(data) > self.max_bytes:
            raise ImageAnalysisError(f"Image exceeds the {self.max_bytes} byte limit")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    image_format = (image.format or "").upper()
                    if image_format not in self.allowed_formats:
                        raise ImageAnalysisError("Only PNG and JPEG images are supported")
                    if getattr(image, "n_frames", 1) != 1:
                        raise ImageAnalysisError("Animated or multi-frame images are not supported")
                    self._validate_dimensions(*image.size)
                    image = ImageOps.exif_transpose(image)
                    width, height = image.size
                    self._validate_dimensions(width, height)
                    image.load()
                    grayscale = np.asarray(image.convert("L"), dtype=np.uint8)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ImageAnalysisError("Image is invalid, truncated, or unsafe to decode") from exc
        except Image.DecompressionBombWarning as exc:
            raise ImageAnalysisError("Decoded image exceeds the safe pixel limit") from exc

        return _DecodedImage(
            grayscale=grayscale,
            image_format=image_format,
            width=width,
            height=height,
        )

    def _validate_dimensions(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ImageAnalysisError("Image dimensions must be positive")
        if width > self.max_dimension or height > self.max_dimension:
            raise ImageAnalysisError(
                f"Image dimensions exceed {self.max_dimension} pixels per side"
            )
        if width * height > self.max_pixels:
            raise ImageAnalysisError(f"Decoded image exceeds the {self.max_pixels} pixel limit")

    @staticmethod
    def _select_contours(grayscale: np.ndarray) -> _ContourSelection:
        blurred = cv2.GaussianBlur(grayscale, (5, 5), 0)
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates: list[tuple[float, _ContourSelection]] = []
        image_area = float(grayscale.shape[0] * grayscale.shape[1])

        for mask in (binary, cv2.bitwise_not(binary)):
            kernel = np.ones((3, 3), dtype=np.uint8)
            cleaned = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, hierarchy = cv2.findContours(
                cleaned,
                cv2.RETR_TREE,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            if hierarchy is None:
                continue
            external = [
                index
                for index, item in enumerate(hierarchy[0])
                if int(item[3]) == -1
            ]
            for index in external:
                area = float(cv2.contourArea(contours[index]))
                ratio = area / image_area
                if ratio < 0.02 or ratio > 0.92:
                    continue
                x, y, width, height = cv2.boundingRect(contours[index])
                touches = x <= 1 or y <= 1 or x + width >= grayscale.shape[1] - 1 or y + height >= grayscale.shape[0] - 1
                score = area * (0.5 if touches else 1.0)
                candidates.append(
                    (
                        score,
                        _ContourSelection(
                            contours=tuple(contours),
                            hierarchy=hierarchy,
                            outer_index=index,
                        ),
                    )
                )

        if not candidates:
            raise ImageAnalysisError("No isolated foreground object was found")
        return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _rectangle_axes(
        box: np.ndarray,
    ) -> tuple[float, float, np.ndarray, np.ndarray]:
        edges = [box[(index + 1) % 4] - box[index] for index in range(4)]
        lengths = [float(np.linalg.norm(edge)) for edge in edges]
        long_index = int(np.argmax(lengths))
        unit_x = edges[long_index] / lengths[long_index]
        if unit_x[0] < 0 or (abs(unit_x[0]) < 1e-9 and unit_x[1] < 0):
            unit_x = -unit_x
        unit_y = np.asarray((unit_x[1], -unit_x[0]), dtype=np.float64)
        return max(lengths), min(lengths), unit_x, unit_y

    @staticmethod
    def _quadrilateral_is_rectangular(approx: np.ndarray) -> bool:
        if len(approx) != 4:
            return False
        points = approx[:, 0, :].astype(np.float64)
        edges = [
            points[(index + 1) % 4] - points[index]
            for index in range(4)
        ]
        lengths = [float(np.linalg.norm(edge)) for edge in edges]
        if min(lengths) < 5:
            return False
        units = [edge / length for edge, length in zip(edges, lengths, strict=True)]
        corner_limit = math.sin(math.radians(5))
        if any(
            abs(float(np.dot(units[index - 1], units[index]))) > corner_limit
            for index in range(4)
        ):
            return False
        parallel_limit = math.sin(math.radians(3))
        def cross_2d(left: np.ndarray, right: np.ndarray) -> float:
            return float(left[0] * right[1] - left[1] * right[0])

        if abs(cross_2d(units[0], units[2])) > parallel_limit:
            return False
        if abs(cross_2d(units[1], units[3])) > parallel_limit:
            return False
        opposite_ratios = (
            min(lengths[0], lengths[2]) / max(lengths[0], lengths[2]),
            min(lengths[1], lengths[3]) / max(lengths[1], lengths[3]),
        )
        return min(opposite_ratios) >= 0.97

    @staticmethod
    def _circles(
        selected: _ContourSelection,
        *,
        outer_area: float,
        center: np.ndarray,
        unit_x: np.ndarray,
        unit_y: np.ndarray,
        mm_per_pixel: float,
        max_diameter_px: float,
    ) -> list[DetectedCircle]:
        found: list[tuple[float, float, float, float, float, float]] = []
        hierarchy = selected.hierarchy[0]
        for index, contour in enumerate(selected.contours):
            if int(hierarchy[index][3]) != selected.outer_index:
                continue
            area = float(cv2.contourArea(contour))
            if area < max(20.0, outer_area * 0.00015):
                continue
            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0:
                continue
            circularity = min(1.0, 4 * math.pi * area / (perimeter * perimeter))
            if circularity < 0.72:
                continue
            (px, py), _radius = cv2.minEnclosingCircle(contour)
            diameter_px = 2 * math.sqrt(area / math.pi)
            if diameter_px >= max_diameter_px:
                continue
            delta = np.asarray((px, py), dtype=np.float64) - center
            x_mm = float(np.dot(delta, unit_x) * mm_per_pixel)
            y_mm = float(np.dot(delta, unit_y) * mm_per_pixel)
            found.append((x_mm, y_mm, diameter_px * mm_per_pixel, circularity, px, py))

        found.sort(key=lambda item: (round(item[0], 8), round(item[1], 8), round(item[2], 8)))
        return [
            DetectedCircle(
                id=f"circle-{index:02d}",
                center_px=PixelPoint(x=float(item[4]), y=float(item[5])),
                center_mm=MetricPoint(x=item[0], y=item[1]),
                diameter_mm=item[2],
                circularity=item[3],
                confidence=min(1.0, item[3]),
            )
            for index, item in enumerate(found, start=1)
        ]

    @staticmethod
    def _feature_tree(
        *,
        length_mm: float,
        width_mm: float,
        thickness_mm: float,
        circles: list[DetectedCircle],
        profile_confidence: float,
    ) -> list[FeatureTreeNode]:
        nodes = [
            FeatureTreeNode(
                id="profile-01",
                operation="sketch_rectangle",
                parameters={"length_mm": length_mm, "width_mm": width_mm},
                confidence=profile_confidence,
            ),
            FeatureTreeNode(
                id="extrude-01",
                operation="extrude",
                parent_id="profile-01",
                parameters={"distance_mm": thickness_mm},
                confidence=profile_confidence,
            ),
        ]
        for circle in circles:
            sketch_id = f"sketch-{circle.id}"
            nodes.append(
                FeatureTreeNode(
                    id=sketch_id,
                    operation="sketch_circle",
                    parent_id="profile-01",
                    parameters={
                        "x_mm": circle.center_mm.x,
                        "y_mm": circle.center_mm.y,
                        "diameter_mm": circle.diameter_mm,
                    },
                    confidence=circle.confidence,
                )
            )
            nodes.append(
                FeatureTreeNode(
                    id=f"cut-{circle.id}",
                    operation="cut_through",
                    parent_id=sketch_id,
                    parameters={"depth_mm": thickness_mm},
                    confidence=circle.confidence,
                )
            )
        return nodes
