from __future__ import annotations

from pathlib import Path

from app.models.cad import (
    CadDocument,
    CylinderBase,
    EnclosureBase,
    LBracketBase,
    PlateBase,
    ProfileExtrusionBase,
    RingBase,
    SideFace,
)
from app.services.profile_geometry import loop_bounds, loop_polyline


class EngineeringDrawingPdf:
    """Create a dependency-free A4 landscape orthographic drawing draft."""

    page_width = 841.89
    page_height = 595.28

    def write(self, doc: CadDocument, path: Path) -> None:
        path.write_bytes(self.render(doc))

    def render(self, doc: CadDocument) -> bytes:
        dim_x, dim_y, dim_z = self._dimensions(doc)
        commands = [
            "0.25 w",
            "0 G",
            self._text(36, 560, 16, "PROMPTCAD ENGINEERING DRAWING"),
            self._text(36, 542, 8, "ORTHOGRAPHIC DRAFT - REVIEW DIMENSIONS BEFORE MANUFACTURING"),
        ]
        commands.extend(self._top_view(doc, 230, 360, 320, 180, dim_x, dim_y))
        commands.extend(self._front_view(doc, 230, 155, 320, 120, dim_x, dim_z))
        commands.extend(self._right_view(doc, 600, 155, 160, 120, dim_y, dim_z))
        commands.extend(self._title_block(doc, dim_x, dim_y, dim_z))
        content = "\n".join(commands).encode("ascii")
        return self._pdf(content, doc.name)

    def _top_view(
        self,
        doc: CadDocument,
        cx: float,
        cy: float,
        max_width: float,
        max_height: float,
        dim_x: float,
        dim_y: float,
    ) -> list[str]:
        scale = min(max_width / dim_x, max_height / dim_y)
        width, height = dim_x * scale, dim_y * scale
        left, bottom = cx - width / 2, cy - height / 2
        commands = [self._text(left, bottom + height + 16, 9, "TOP VIEW")]
        base = doc.base
        origin_x, origin_y = 0.0, 0.0

        if isinstance(base, ProfileExtrusionBase):
            min_x, min_y, max_x, max_y = loop_bounds(base.outer)
            origin_x, origin_y = (min_x + max_x) / 2, (min_y + max_y) / 2
            points = [
                (left + (x - min_x) * scale, bottom + (y - min_y) * scale)
                for x, y in loop_polyline(base.outer)
            ]
            commands.append(self._polyline(points, close=True))
        elif isinstance(base, (CylinderBase, RingBase)):
            commands.append(self._circle(cx, cy, width / 2))
            if isinstance(base, RingBase):
                commands.append(self._circle(cx, cy, base.inner_diameter * scale / 2))
        else:
            commands.append(self._rect(left, bottom, width, height))
            if isinstance(base, EnclosureBase):
                inset = base.wall_thickness * scale
                commands.extend(
                    [
                        "[3 2] 0 d",
                        self._rect(
                            left + inset,
                            bottom + inset,
                            width - 2 * inset,
                            height - 2 * inset,
                        ),
                        "[] 0 d",
                    ]
                )
            if isinstance(base, LBracketBase):
                commands.append(
                    self._line(
                        left,
                        bottom + base.thickness * scale,
                        left + width,
                        bottom + base.thickness * scale,
                    )
                )

        for hole in doc.holes:
            if hole.axis.value != "z":
                continue
            x, y = cx + (hole.x - origin_x) * scale, cy + (hole.y - origin_y) * scale
            radius = hole.diameter * scale / 2
            commands.extend(
                [
                    self._circle(x, y, radius),
                    "[2 2] 0 d",
                    self._line(x - radius - 4, y, x + radius + 4, y),
                    self._line(x, y - radius - 4, x, y + radius + 4),
                    "[] 0 d",
                ]
            )

        if isinstance(base, EnclosureBase):
            wall = max(base.wall_thickness * scale, 2)
            for cutout in doc.cutouts:
                span = cutout.width * scale
                if cutout.face == SideFace.POSITIVE_Y:
                    commands.append(
                        self._rect(cx + cutout.x * scale - span / 2, bottom + height - wall, span, wall)
                    )
                elif cutout.face == SideFace.NEGATIVE_Y:
                    commands.append(
                        self._rect(cx + cutout.x * scale - span / 2, bottom, span, wall)
                    )
                elif cutout.face == SideFace.POSITIVE_X:
                    commands.append(
                        self._rect(left + width - wall, cy + cutout.y * scale - span / 2, wall, span)
                    )
                else:
                    commands.append(
                        self._rect(left, cy + cutout.y * scale - span / 2, wall, span)
                    )

        commands.extend(
            self._horizontal_dimension(left, left + width, bottom - 22, f"{dim_x:g} mm")
        )
        commands.extend(
            self._vertical_dimension(left - 22, bottom, bottom + height, f"{dim_y:g} mm")
        )
        return commands

    def _front_view(
        self,
        doc: CadDocument,
        cx: float,
        cy: float,
        max_width: float,
        max_height: float,
        dim_x: float,
        dim_z: float,
    ) -> list[str]:
        scale = min(max_width / dim_x, max_height / dim_z)
        width, height = dim_x * scale, dim_z * scale
        left, bottom = cx - width / 2, cy - height / 2
        commands = [
            self._text(left, bottom + height + 16, 9, "FRONT VIEW"),
            self._rect(left, bottom, width, height),
        ]
        origin_x = 0.0
        if isinstance(doc.base, ProfileExtrusionBase):
            min_x, _, max_x, _ = loop_bounds(doc.base.outer)
            origin_x = (min_x + max_x) / 2
        for hole in doc.holes:
            if hole.axis.value == "y":
                commands.append(
                    self._circle(
                        cx + (hole.x - origin_x) * scale,
                        bottom + hole.z * scale,
                        hole.diameter * scale / 2,
                    )
                )
        if isinstance(doc.base, EnclosureBase):
            inner_left = left + doc.base.wall_thickness * scale
            inner_width = width - 2 * doc.base.wall_thickness * scale
            inner_bottom = bottom + doc.base.wall_thickness * scale
            commands.extend(
                [
                    "[3 2] 0 d",
                    self._line(inner_left, inner_bottom, inner_left + inner_width, inner_bottom),
                    self._line(inner_left, inner_bottom, inner_left, bottom + height),
                    self._line(inner_left + inner_width, inner_bottom, inner_left + inner_width, bottom + height),
                    "[] 0 d",
                ]
            )
            for cutout in doc.cutouts:
                if cutout.face not in {SideFace.POSITIVE_Y, SideFace.NEGATIVE_Y}:
                    continue
                x = cx + cutout.x * scale - cutout.width * scale / 2
                y = bottom + cutout.z * scale - cutout.height * scale / 2
                commands.extend(
                    self._opening_symbol(x, y, cutout.width * scale, cutout.height * scale)
                )
        commands.extend(
            self._vertical_dimension(left - 22, bottom, bottom + height, f"{dim_z:g} mm")
        )
        return commands

    def _right_view(
        self,
        doc: CadDocument,
        cx: float,
        cy: float,
        max_width: float,
        max_height: float,
        dim_y: float,
        dim_z: float,
    ) -> list[str]:
        scale = min(max_width / dim_y, max_height / dim_z)
        width, height = dim_y * scale, dim_z * scale
        left, bottom = cx - width / 2, cy - height / 2
        commands = [
            self._text(left, bottom + height + 16, 9, "RIGHT VIEW"),
            self._rect(left, bottom, width, height),
        ]
        origin_y = 0.0
        if isinstance(doc.base, ProfileExtrusionBase):
            _, min_y, _, max_y = loop_bounds(doc.base.outer)
            origin_y = (min_y + max_y) / 2
        for hole in doc.holes:
            if hole.axis.value == "x":
                commands.append(
                    self._circle(
                        cx + (hole.y - origin_y) * scale,
                        bottom + hole.z * scale,
                        hole.diameter * scale / 2,
                    )
                )
        if isinstance(doc.base, EnclosureBase):
            for cutout in doc.cutouts:
                if cutout.face not in {SideFace.POSITIVE_X, SideFace.NEGATIVE_X}:
                    continue
                x = cx + cutout.y * scale - cutout.width * scale / 2
                y = bottom + cutout.z * scale - cutout.height * scale / 2
                commands.extend(
                    self._opening_symbol(x, y, cutout.width * scale, cutout.height * scale)
                )
        commands.extend(
            self._horizontal_dimension(left, left + width, bottom - 22, f"{dim_y:g} mm")
        )
        return commands

    def _title_block(
        self,
        doc: CadDocument,
        dim_x: float,
        dim_y: float,
        dim_z: float,
    ) -> list[str]:
        x, y, width, height = 510, 34, 296, 64
        material = doc.material.value if doc.material else "unspecified"
        return [
            self._rect(x, y, width, height),
            self._line(x, y + 22, x + width, y + 22),
            self._line(x, y + 43, x + width, y + 43),
            self._text(x + 8, y + 48, 9, f"PART: {self._ascii(doc.name)}"),
            self._text(x + 8, y + 28, 8, f"MATERIAL: {self._ascii(material)}"),
            self._text(x + 170, y + 28, 8, "UNITS: mm"),
            self._text(x + 8, y + 8, 8, f"SIZE: {dim_x:g} x {dim_y:g} x {dim_z:g} mm"),
            self._text(x + 170, y + 8, 8, "PROJECTION: 3RD ANGLE"),
        ]

    @staticmethod
    def _horizontal_dimension(
        start: float,
        end: float,
        y: float,
        label: str,
    ) -> list[str]:
        middle = (start + end) / 2
        return [
            EngineeringDrawingPdf._line(start, y, end, y),
            EngineeringDrawingPdf._line(start, y - 4, start, y + 4),
            EngineeringDrawingPdf._line(end, y - 4, end, y + 4),
            EngineeringDrawingPdf._text(middle - len(label) * 2.2, y + 6, 8, label),
        ]

    @staticmethod
    def _vertical_dimension(
        x: float,
        start: float,
        end: float,
        label: str,
    ) -> list[str]:
        return [
            EngineeringDrawingPdf._line(x, start, x, end),
            EngineeringDrawingPdf._line(x - 4, start, x + 4, start),
            EngineeringDrawingPdf._line(x - 4, end, x + 4, end),
            EngineeringDrawingPdf._text(x - 18, (start + end) / 2, 8, label),
        ]

    @staticmethod
    def _opening_symbol(x: float, y: float, width: float, height: float) -> list[str]:
        return [
            EngineeringDrawingPdf._rect(x, y, width, height),
            EngineeringDrawingPdf._line(x, y, x + width, y + height),
            EngineeringDrawingPdf._line(x, y + height, x + width, y),
        ]

    @staticmethod
    def _line(x1: float, y1: float, x2: float, y2: float) -> str:
        return f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"

    @staticmethod
    def _rect(x: float, y: float, width: float, height: float) -> str:
        return f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re S"

    @staticmethod
    def _polyline(points: list[tuple[float, float]], close: bool = False) -> str:
        if not points:
            return ""
        command = f"{points[0][0]:.2f} {points[0][1]:.2f} m"
        command += "".join(f" {x:.2f} {y:.2f} l" for x, y in points[1:])
        return command + (" h S" if close else " S")

    @staticmethod
    def _circle(cx: float, cy: float, radius: float) -> str:
        k = radius * 0.5522847498
        return (
            f"{cx + radius:.2f} {cy:.2f} m "
            f"{cx + radius:.2f} {cy + k:.2f} {cx + k:.2f} {cy + radius:.2f} "
            f"{cx:.2f} {cy + radius:.2f} c "
            f"{cx - k:.2f} {cy + radius:.2f} {cx - radius:.2f} {cy + k:.2f} "
            f"{cx - radius:.2f} {cy:.2f} c "
            f"{cx - radius:.2f} {cy - k:.2f} {cx - k:.2f} {cy - radius:.2f} "
            f"{cx:.2f} {cy - radius:.2f} c "
            f"{cx + k:.2f} {cy - radius:.2f} {cx + radius:.2f} {cy - k:.2f} "
            f"{cx + radius:.2f} {cy:.2f} c S"
        )

    @staticmethod
    def _text(x: float, y: float, size: float, value: str) -> str:
        escaped = EngineeringDrawingPdf._ascii(value).replace("\\", "\\\\")
        escaped = escaped.replace("(", "\\(").replace(")", "\\)")
        return f"BT /F1 {size:g} Tf {x:.2f} {y:.2f} Td ({escaped}) Tj ET"

    @staticmethod
    def _ascii(value: str) -> str:
        return value.encode("ascii", "replace").decode("ascii")

    @staticmethod
    def _dimensions(doc: CadDocument) -> tuple[float, float, float]:
        base = doc.base
        if isinstance(base, PlateBase):
            return base.length, base.width, base.thickness
        if isinstance(base, CylinderBase):
            return base.diameter, base.diameter, base.height
        if isinstance(base, RingBase):
            return base.outer_diameter, base.outer_diameter, base.height
        if isinstance(base, LBracketBase):
            return base.width, base.depth, base.vertical_height + base.thickness
        if isinstance(base, ProfileExtrusionBase):
            min_x, min_y, max_x, max_y = loop_bounds(base.outer)
            return max_x - min_x, max_y - min_y, base.thickness
        return base.length, base.width, base.height

    @staticmethod
    def _pdf(content: bytes, title: str) -> bytes:
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 841.89 595.28] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            ),
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            (
                b"<< /Title ("
                + EngineeringDrawingPdf._ascii(title).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")
                + b") /Creator (PromptCAD Studio) >>"
            ),
        ]
        output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for number, obj in enumerate(objects, start=1):
            offsets.append(len(output))
            output.extend(f"{number} 0 obj\n".encode("ascii"))
            output.extend(obj)
            output.extend(b"\nendobj\n")
        xref = len(output)
        output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        output.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n"
            ).encode("ascii")
        )
        return bytes(output)
