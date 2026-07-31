from __future__ import annotations

import hashlib
import io
import math
import threading
import warnings
from contextlib import suppress
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models.cad import (
    Axis,
    CadDocument,
    HoleFeature,
    HoleType,
    LineSegment2D,
    PlannerMetadata,
    PlateBase,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
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


_PDFIUM_LOCK = threading.Lock()


@dataclass(frozen=True)
class _DecodedImage:
    grayscale: np.ndarray
    image_format: str
    width: int
    height: int
    source_kind: str = "image"
    page_index: int | None = None
    page_count: int | None = None


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
        max_pdf_pages: int = 20,
    ):
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.max_dimension = max_dimension
        self.max_pdf_pages = max_pdf_pages
        self.validator = DesignValidator()
        self.preview = SvgPreview()

    def analyze(
        self,
        data: bytes,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool = False,
        page_index: int = 0,
    ) -> ImageAnalysisResponse:
        if not math.isfinite(known_length_mm) or known_length_mm <= 0:
            raise ImageAnalysisError("known_length_mm must be a positive finite number")
        if not math.isfinite(thickness_mm) or thickness_mm <= 0:
            raise ImageAnalysisError("thickness_mm must be a positive finite number")

        decoded = self._decode(data, page_index=page_index)
        image_sha256 = hashlib.sha256(data).hexdigest()
        source_width = decoded.width
        source_height = decoded.height
        grayscale = decoded.grayscale
        selected = self._select_contours(grayscale)
        perspective_corrected = False
        if perspective_correction:
            grayscale = self._rectify_quadrilateral(grayscale, selected)
            self._validate_dimensions(grayscale.shape[1], grayscale.shape[0])
            selected = self._select_contours(grayscale)
            perspective_corrected = True
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
        rectangular_convertible = (
            len(approx) == 4
            and rectangularity >= 0.90
            and rectangular_geometry
        )
        profile_approx = cv2.approxPolyDP(outer, 0.0075 * perimeter, True)
        hull_area = float(cv2.contourArea(cv2.convexHull(outer)))
        solidity = min(1.0, outer_area / max(hull_area, 1.0))
        approximation_area = abs(float(cv2.contourArea(profile_approx)))
        area_fidelity = min(1.0, approximation_area / max(outer_area, 1.0))
        ambiguous_perspective_quad = (
            not perspective_corrected
            and len(profile_approx) == 4
            and bool(cv2.isContourConvex(profile_approx))
        )
        profile_convertible = (
            not rectangular_convertible
            and not ambiguous_perspective_quad
            and 3 <= len(profile_approx) <= 128
            and solidity >= 0.55
            and area_fidelity >= 0.88
        )
        convertible = rectangular_convertible or profile_convertible
        profile_confidence = min(
            1.0,
            rectangularity if rectangular_convertible else solidity * area_fidelity,
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
        profile_points = (
            self._metric_profile_points(
                profile_approx,
                center=center,
                unit_x=unit_x,
                unit_y=unit_y,
                mm_per_pixel=mm_per_pixel,
            )
            if profile_convertible
            else []
        )
        outer_profile = DetectedOuterProfile(
            shape=(
                "rectangle"
                if rectangular_convertible
                else "profile"
                if profile_convertible
                else "unsupported"
            ),
            length_mm=known_length_mm if convertible else None,
            width_mm=width_mm if convertible else None,
            points_mm=profile_points,
            rotation_deg=rotation_deg,
            rectangularity=rectangularity,
            confidence=profile_confidence,
            perspective_corrected=perspective_corrected,
        )

        warnings_out = [
            "比例由外框最長邊校準；不使用圖片 DPI 或 EXIF 尺寸。",
            "厚度由使用者指定，無法從單張俯視圖可靠推定。",
            "影像結果預設需要人工確認；透視、遮擋或反光照片不應直接製造。",
        ]
        if decoded.source_kind == "pdf":
            warnings_out.append(
                f"PDF 第 {decoded.page_index + 1} 頁已光柵化；文字與尺寸標註不會自動視為幾何。"
            )
        if perspective_corrected:
            warnings_out.append(
                "已依四角執行透視校正；只適用原物為矩形板的照片，必須人工覆核。"
            )
        proposed_spec = None
        validation = None
        feature_tree: list[FeatureTreeNode] = []

        if convertible:
            feature_tree = (
                self._feature_tree(
                    length_mm=known_length_mm,
                    width_mm=width_mm,
                    thickness_mm=thickness_mm,
                    circles=circles,
                    profile_confidence=profile_confidence,
                )
                if rectangular_convertible
                else self._profile_feature_tree(
                    points=profile_points,
                    thickness_mm=thickness_mm,
                    circles=circles,
                    profile_confidence=profile_confidence,
                )
            )
            proposed_spec = self.spec_from_feature_tree(
                feature_tree,
                image_sha256=image_sha256,
                extra_notes=[
                    f"原始格式：{decoded.image_format}",
                    f"來源頁面：{decoded.page_index + 1}"
                    if decoded.page_index is not None
                    else "來源頁面：不適用",
                ],
            )
            validation = self.validator.validate(proposed_spec)
            if not validation.valid:
                warnings_out.append("擷取幾何未通過 CAD 驗證，請修正 Feature Tree 後再輸出。")
        else:
            warnings_out.append("外框信心或簡化品質不足；已停止自動 CAD 轉換。")

        return ImageAnalysisResponse(
            image_sha256=image_sha256,
            image_format=decoded.image_format,
            source_kind=decoded.source_kind,
            source_page_index=decoded.page_index,
            source_page_count=decoded.page_count,
            source_image_width_px=source_width,
            source_image_height_px=source_height,
            image_width_px=grayscale.shape[1],
            image_height_px=grayscale.shape[0],
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

        rectangle_profiles = [
            node for node in feature_tree if node.operation == "sketch_rectangle"
        ]
        free_profiles = [
            node for node in feature_tree if node.operation == "sketch_profile"
        ]
        profiles = [*rectangle_profiles, *free_profiles]
        extrusions = [node for node in feature_tree if node.operation == "extrude"]
        circle_nodes = [node for node in feature_tree if node.operation == "sketch_circle"]
        cut_nodes = [node for node in feature_tree if node.operation == "cut_through"]
        if len(profiles) != 1 or len(extrusions) != 1:
            raise ImageAnalysisError(
                "Feature Tree requires exactly one outer profile and one extrusion"
            )
        profile = profiles[0]
        extrusion = extrusions[0]
        self._require_parameters(extrusion, {"distance_mm"})
        if extrusion.parent_id != profile.id:
            raise ImageAnalysisError("Extrusion must reference the outer profile")

        if profile.operation == "sketch_rectangle":
            self._require_parameters(profile, {"length_mm", "width_mm"})
            if profile.points:
                raise ImageAnalysisError("Rectangle profile must not contain point geometry")
            base = PlateBase(
                length=profile.parameters["length_mm"],
                width=profile.parameters["width_mm"],
                thickness=extrusion.parameters["distance_mm"],
            )
            schema_version = "1.0"
            source_prompt = "由可編輯影像 Feature Tree 建立矩形板與圓孔"
        else:
            self._require_parameters(profile, set())
            if len(profile.points) < 3:
                raise ImageAnalysisError("Free profile requires at least three points")
            if len({(point.x, point.y) for point in profile.points}) != len(profile.points):
                raise ImageAnalysisError("Free profile points must be unique")
            signed_area = sum(
                left.x * right.y - right.x * left.y
                for left, right in zip(
                    profile.points,
                    [*profile.points[1:], profile.points[0]],
                    strict=True,
                )
            ) / 2
            if abs(signed_area) < 1e-6:
                raise ImageAnalysisError("Free profile area must be non-zero")
            profile_points = [Point2D(x=point.x, y=point.y) for point in profile.points]
            segments = [
                LineSegment2D(start=left, end=right)
                for left, right in zip(
                    profile_points,
                    [*profile_points[1:], profile_points[0]],
                    strict=True,
                )
            ]
            base = ProfileExtrusionBase(
                outer=ProfileLoop2D(segments=segments),
                thickness=extrusion.parameters["distance_mm"],
            )
            schema_version = "1.1"
            source_prompt = "由可編輯影像 Feature Tree 建立自由閉合輪廓與圓孔"

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
            schema_version=schema_version,
            name="image-extracted-plate",
            source_prompt=source_prompt,
            base=base,
            holes=holes,
            assumptions=[
                "比例由外框最長邊校準；不使用圖片 DPI 或 EXIF 尺寸。",
                "厚度由使用者指定，無法從單張俯視圖可靠推定。",
                "影像結果需要人工確認；透視、遮擋、反光或自由輪廓簡化不應直接製造。",
            ],
            notes=[
                f"影像 SHA-256：{image_sha256}",
                "分析版本：1.1",
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

    def _decode(self, data: bytes, *, page_index: int) -> _DecodedImage:
        if not data:
            raise ImageAnalysisError("Image file is empty")
        if len(data) > self.max_bytes:
            raise ImageAnalysisError(f"Image exceeds the {self.max_bytes} byte limit")
        if data.startswith(b"%PDF-"):
            return self._decode_pdf(data, page_index=page_index)
        if page_index != 0:
            raise ImageAnalysisError("page_index is only supported for PDF input")

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

    def _decode_pdf(self, data: bytes, *, page_index: int) -> _DecodedImage:
        if page_index < 0:
            raise ImageAnalysisError("PDF page_index must be zero or greater")
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise ImageAnalysisError(
                "PDF support requires the pypdfium2 runtime dependency"
            ) from exc

        document = None
        page = None
        bitmap = None
        with _PDFIUM_LOCK:
            try:
                document = pdfium.PdfDocument(data)
                page_count = len(document)
                if page_count < 1:
                    raise ImageAnalysisError("PDF contains no pages")
                if page_count > self.max_pdf_pages:
                    raise ImageAnalysisError(
                        f"PDF exceeds the {self.max_pdf_pages} page limit"
                    )
                if page_index >= page_count:
                    raise ImageAnalysisError(
                        f"PDF page_index {page_index} is outside {page_count} pages"
                    )
                page = document[page_index]
                page_width, page_height = page.get_size()
                if page_width <= 0 or page_height <= 0:
                    raise ImageAnalysisError("PDF page dimensions are invalid")
                scale = min(
                    2.0,
                    (self.max_dimension - 1) / max(page_width, page_height),
                    math.sqrt((self.max_pixels * 0.98) / (page_width * page_height)),
                )
                if not math.isfinite(scale) or scale <= 0:
                    raise ImageAnalysisError("PDF page cannot be rasterized safely")
                bitmap = page.render(
                    scale=scale,
                    rotation=0,
                    fill_color=(255, 255, 255, 255),
                )
                pil_image = bitmap.to_pil().convert("L").copy()
            except ImageAnalysisError:
                raise
            except Exception as exc:
                raise ImageAnalysisError(
                    "PDF is encrypted, invalid, truncated, or unsafe to render"
                ) from exc
            finally:
                # PDFium is not thread-safe. Keep teardown under the same lock as
                # document rendering so native handles never overlap across jobs.
                for resource in (bitmap, page, document):
                    if resource is not None:
                        with suppress(Exception):
                            resource.close()

        width, height = pil_image.size
        self._validate_dimensions(width, height)
        return _DecodedImage(
            grayscale=np.asarray(pil_image, dtype=np.uint8),
            image_format="PDF",
            width=width,
            height=height,
            source_kind="pdf",
            page_index=page_index,
            page_count=page_count,
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
    def _rectify_quadrilateral(
        grayscale: np.ndarray,
        selected: _ContourSelection,
    ) -> np.ndarray:
        outer = selected.contours[selected.outer_index]
        perimeter = float(cv2.arcLength(outer, True))
        approx = cv2.approxPolyDP(outer, 0.02 * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            raise ImageAnalysisError(
                "Perspective correction requires one isolated convex four-corner profile"
            )
        points = approx[:, 0, :].astype(np.float32)
        sums = points.sum(axis=1)
        differences = np.diff(points, axis=1).reshape(-1)
        ordered = np.asarray(
            [
                points[int(np.argmin(sums))],
                points[int(np.argmin(differences))],
                points[int(np.argmax(sums))],
                points[int(np.argmax(differences))],
            ],
            dtype=np.float32,
        )
        top_left, top_right, bottom_right, bottom_left = ordered
        width = max(
            float(np.linalg.norm(bottom_right - bottom_left)),
            float(np.linalg.norm(top_right - top_left)),
        )
        height = max(
            float(np.linalg.norm(top_right - bottom_right)),
            float(np.linalg.norm(top_left - bottom_left)),
        )
        if width < 20 or height < 20:
            raise ImageAnalysisError(
                "Perspective profile is too small for reliable correction"
            )
        padding = max(8, int(round(min(width, height) * 0.08)))
        output_width = int(math.ceil(width)) + padding * 2
        output_height = int(math.ceil(height)) + padding * 2
        destination = np.asarray(
            [
                [padding, padding],
                [padding + width - 1, padding],
                [padding + width - 1, padding + height - 1],
                [padding, padding + height - 1],
            ],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(ordered, destination)
        return cv2.warpPerspective(
            grayscale,
            transform,
            (output_width, output_height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )

    @staticmethod
    def _metric_profile_points(
        approx: np.ndarray,
        *,
        center: np.ndarray,
        unit_x: np.ndarray,
        unit_y: np.ndarray,
        mm_per_pixel: float,
    ) -> list[MetricPoint]:
        points = []
        for value in approx[:, 0, :].astype(np.float64):
            delta = value - center
            points.append(
                MetricPoint(
                    x=round(float(np.dot(delta, unit_x) * mm_per_pixel), 6),
                    y=round(float(np.dot(delta, unit_y) * mm_per_pixel), 6),
                )
            )
        return points

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

    @staticmethod
    def _profile_feature_tree(
        *,
        points: list[MetricPoint],
        thickness_mm: float,
        circles: list[DetectedCircle],
        profile_confidence: float,
    ) -> list[FeatureTreeNode]:
        nodes = [
            FeatureTreeNode(
                id="profile-01",
                operation="sketch_profile",
                points=points,
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
