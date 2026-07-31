from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models.cad import (
    CadDocument,
    CylinderBase,
    EnclosureBase,
    LBracketBase,
    PlateBase,
    ProfileExtrusionBase,
    ProfileRevolutionBase,
    RingBase,
    SideFace,
)
from app.models.manufacturing import ManufacturingDrawingSpec, ResolvedDimension
from app.services.manufacturing import ManufacturingDrawingService
from app.services.profile_geometry import loop_bounds, loop_polyline


class EngineeringDrawingPdf:
    """Create a dependency-free A4 landscape orthographic drawing draft."""

    page_width = 841.89
    page_height = 595.28

    def write(
        self,
        doc: CadDocument,
        path: Path,
        manufacturing: ManufacturingDrawingSpec | dict[str, Any] | None = None,
        review_summary: Any | None = None,
    ) -> None:
        path.write_bytes(self.render(doc, manufacturing, review_summary))

    def render(
        self,
        doc: CadDocument,
        manufacturing: ManufacturingDrawingSpec | dict[str, Any] | None = None,
        review_summary: Any | None = None,
    ) -> bytes:
        if manufacturing is not None:
            return self._render_manufacturing(doc, manufacturing, review_summary)
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

    def _render_manufacturing(
        self,
        doc: CadDocument,
        manufacturing: ManufacturingDrawingSpec | dict[str, Any],
        review_summary: Any | None,
    ) -> bytes:
        """Render a bounded two-page, searchable manufacturing drawing package."""

        spec = self._as_mapping(manufacturing)
        resolved_dimensions = (
            ManufacturingDrawingService().resolve_dimensions(doc, manufacturing)
            if isinstance(manufacturing, ManufacturingDrawingSpec)
            else []
        )
        title_block = self._as_mapping(spec.get("title_block"))
        dim_x, dim_y, dim_z = self._dimensions(doc)
        tolerance = self._general_tolerance(spec)
        datum = self._joined_field(spec, "datums", "datum_scheme", "datum", default="A")
        surface = self._surface_finish(spec)
        status = self._first_text(
            self._as_mapping(review_summary),
            "status",
            "workflow_status",
            "decision",
            default=self._first_text(
                spec,
                "review_status",
                "workflow_status",
                "status",
                default="DRAFT",
            ),
        )
        drawing_number = self._first_text(
            title_block or spec,
            "drawing_number",
            "part_number",
            "number",
            default=doc.name,
        )
        revision = self._first_text(
            title_block or spec,
            "revision",
            "revision_id",
            default="A",
        )

        page_one = [
            "0.25 w",
            "0 G",
            self._text(36, 560, 16, "PROMPTCAD MANUFACTURING DRAWING"),
            self._text(
                36,
                542,
                8,
                "CONTROLLED DRAFT - VERIFY AGAINST RELEASED CAD BEFORE MANUFACTURING",
            ),
        ]
        page_one.extend(self._top_view(doc, 230, 360, 320, 180, dim_x, dim_y))
        page_one.extend(self._front_view(doc, 230, 155, 320, 120, dim_x, dim_z))
        page_one.extend(self._right_view(doc, 600, 155, 160, 120, dim_y, dim_z))
        page_one.extend(
            self._manufacturing_title_block(
                doc,
                title_block,
                dim_x,
                dim_y,
                dim_z,
            )
        )
        page_one.extend(
            [
                self._text(36, 112, 8, f"DRAWING NO: {drawing_number}  REV: {revision}"),
                self._text(
                    36,
                    100,
                    8,
                    f"OVERALL: {dim_x:g} x {dim_y:g} x {dim_z:g} mm  TOL: {tolerance}",
                ),
                self._text(36, 88, 8, f"DATUM: {datum}"),
                self._text(36, 76, 8, f"SURFACE FINISH: {surface}"),
                self._text(36, 64, 8, f"WORKFLOW STATUS: {status}"),
                self._text(
                    36,
                    50,
                    7,
                    "REVIEW RECORD IS NOT A CRYPTOGRAPHIC SIGNATURE OR DIGITAL CERTIFICATE.",
                ),
                self._text(36, 34, 7, "PROMPTCAD STUDIO - PACKAGE PAGE 1 OF 2"),
            ]
        )
        page_one.extend(self._hole_callouts(doc, tolerance))

        page_two = self._manufacturing_details_page(
            doc=doc,
            spec=spec,
            review_summary=review_summary,
            drawing_number=drawing_number,
            revision=revision,
            tolerance=tolerance,
            datum=datum,
            surface=surface,
            status=status,
            resolved_dimensions=resolved_dimensions,
        )
        contents = ["\n".join(page_one).encode("ascii"), "\n".join(page_two).encode("ascii")]
        return self._pdf_pages(contents, f"{doc.name} manufacturing drawing")

    def _hole_callouts(self, doc: CadDocument, tolerance: str) -> list[str]:
        commands = [self._text(520, 520, 9, "HOLE DIMENSIONS / LOCATIONS")]
        if not doc.holes:
            commands.append(self._text(520, 507, 7, "NONE"))
            return commands
        for index, hole in enumerate(doc.holes[:12], start=1):
            thread = f" {hole.thread}" if hole.thread else ""
            label = (
                f"H{index}: DIA {hole.diameter:g} mm{thread}  "
                f"X {hole.x:g} Y {hole.y:g} Z {hole.z:g}  {tolerance}"
            )
            commands.append(self._text(520, 520 - index * 13, 7, label))
        if len(doc.holes) > 12:
            commands.append(self._text(520, 351, 7, f"... {len(doc.holes) - 12} MORE HOLES"))
        return commands

    def _manufacturing_title_block(
        self,
        doc: CadDocument,
        title: dict[str, Any],
        dim_x: float,
        dim_y: float,
        dim_z: float,
    ) -> list[str]:
        x, y, width, height = 510, 22, 296, 76
        part_name = self._first_text(title, "part_name", default=doc.name)
        part_number = self._first_text(title, "part_number", default="-")
        drawing = self._first_text(title, "drawing_number", default=doc.name)
        revision = self._first_text(title, "revision", default="A")
        drawn_by = self._first_text(title, "drawn_by", default="-")
        drawn_on = self._first_text(title, "drawn_on", default="-")
        checked_by = self._first_text(title, "checked_by", default="-")
        approved_by = self._first_text(title, "approved_by", default="-")
        projection = self._first_text(title, "projection", default="third_angle")
        return [
            self._rect(x, y, width, height),
            self._line(x, y + 19, x + width, y + 19),
            self._line(x, y + 38, x + width, y + 38),
            self._line(x, y + 57, x + width, y + 57),
            self._text(x + 7, y + 62, 8, f"PART: {part_name[:42]}  P/N: {part_number[:24]}"),
            self._text(x + 7, y + 43, 8, f"DRAWING: {drawing[:34]}  REV: {revision[:12]}"),
            self._text(
                x + 7,
                y + 24,
                7,
                f"DRAWN: {drawn_by[:18]} {drawn_on[:12]}  CHECKED: {checked_by[:18]}",
            ),
            self._text(
                x + 7,
                y + 6,
                7,
                f"SIZE: {dim_x:g} x {dim_y:g} x {dim_z:g} mm  {projection}  APPROVED: {approved_by[:16]}",
            ),
        ]

    def _manufacturing_details_page(
        self,
        *,
        doc: CadDocument,
        spec: dict[str, Any],
        review_summary: Any | None,
        drawing_number: str,
        revision: str,
        tolerance: str,
        datum: str,
        surface: str,
        status: str,
        resolved_dimensions: list[ResolvedDimension],
    ) -> list[str]:
        commands = [
            "0.25 w",
            "0 G",
            self._text(36, 560, 15, "MANUFACTURING NOTES / BOM / REVISION HISTORY"),
            self._text(36, 542, 8, f"DRAWING NO: {drawing_number}  REV: {revision}"),
            self._text(36, 524, 9, "GENERAL REQUIREMENTS"),
            self._text(48, 510, 8, f"1. UNLESS OTHERWISE SPECIFIED, LINEAR TOLERANCE: {tolerance}"),
            self._text(48, 497, 8, f"2. PRIMARY DATUM(S): {datum}"),
            self._text(48, 484, 8, f"3. SURFACE FINISH: {surface}"),
            self._text(48, 471, 8, "4. BREAK SHARP EDGES; DEBURR AND CLEAN PART."),
            self._text(48, 458, 8, "5. DIMENSIONS ARE IN MILLIMETRES. DO NOT SCALE DRAWING."),
            self._text(438, 524, 9, "CONTROLLED DIMENSION SCHEDULE"),
            *self._resolved_dimension_commands(resolved_dimensions),
            self._text(36, 432, 10, "BILL OF MATERIALS"),
            self._rect(36, 275, 770, 145),
            self._line(36, 400, 806, 400),
            self._text(43, 406, 7, "ITEM | PART / DESCRIPTION | QTY | MATERIAL | NOTE"),
        ]
        bom = self._list_field(spec, "bom", "bill_of_materials", "components")
        if not bom:
            material = doc.material.value if doc.material else "unspecified"
            bom = [{"item": 1, "description": doc.name, "quantity": 1, "material": material}]
        y = 386
        for index, raw_item in enumerate(bom[:9], start=1):
            item = self._as_mapping(raw_item)
            number = self._first_text(item, "item", "item_number", "number", default=str(index))
            part = self._first_text(
                item,
                "part_number",
                "description",
                "name",
                default=f"ITEM {index}",
            )
            quantity = self._first_text(item, "quantity", "qty", default="1")
            material = self._first_text(item, "material", default="-")
            note = self._first_text(item, "note", "notes", default="-")
            commands.append(
                self._text(
                    43,
                    y,
                    7,
                    f"{number} | {part[:48]} | {quantity} | {material[:24]} | {note[:35]}",
                )
            )
            y -= 13
        if len(bom) > 9:
            commands.append(self._text(43, y, 7, f"... {len(bom) - 9} MORE BOM ITEMS"))

        commands.extend(
            [
                self._text(36, 250, 10, "REVISION HISTORY"),
                self._rect(36, 140, 380, 98),
                self._text(43, 224, 7, "REV | DATE | DESCRIPTION | APPROVED BY"),
            ]
        )
        revisions = self._list_field(spec, "revision_history", "revisions")
        if not revisions:
            revisions = [{"revision": revision, "description": "INITIAL MANUFACTURING DRAFT"}]
        y = 210
        for raw_revision in revisions[:5]:
            row = self._as_mapping(raw_revision)
            rev = self._first_text(row, "revision", "rev", default=revision)
            date = self._first_text(row, "occurred_on", "date", "created_at", default="-")
            description = self._first_text(row, "description", "change", default="-")
            approved = self._first_text(row, "approved_by", "author", default="-")
            commands.append(
                self._text(43, y, 7, f"{rev} | {date[:16]} | {description[:35]} | {approved[:20]}")
            )
            y -= 14

        commands.extend(
            [
                self._text(438, 250, 10, "WORKFLOW / REVIEW RECORD"),
                self._rect(438, 140, 368, 98),
                self._text(447, 222, 8, f"STATUS: {status}"),
            ]
        )
        effective_review = review_summary
        if effective_review is None:
            records = self._list_field(spec, "review_records")
            effective_review = records[-1] if records else None
        review_lines = self._review_lines(effective_review)
        for index, line in enumerate(review_lines[:5]):
            commands.append(self._text(447, 207 - index * 13, 7, line[:88]))
        commands.extend(
            [
                self._text(
                    36,
                    112,
                    8,
                    "NOTICE: REVIEW NAMES, STATUS, AND TIMESTAMPS ARE TRACEABILITY METADATA ONLY.",
                ),
                self._text(
                    36,
                    97,
                    8,
                    "THEY ARE NOT A CRYPTOGRAPHIC SIGNATURE OR DIGITAL CERTIFICATE.",
                ),
                self._text(
                    36,
                    78,
                    7,
                    "RELEASE FOR PRODUCTION REQUIRES AUTHORIZED HUMAN APPROVAL AND SOURCE CAD VERIFICATION.",
                ),
                self._text(36, 48, 7, "PROMPTCAD STUDIO - PAGE 2 OF 2"),
            ]
        )
        return commands

    @classmethod
    def _resolved_dimension_commands(
        cls,
        dimensions: list[ResolvedDimension],
    ) -> list[str]:
        if not dimensions:
            return [cls._text(447, 510, 7, "NO CONTROLLED DIMENSIONS SPECIFIED")]
        commands = []
        for index, dimension in enumerate(dimensions[:6]):
            tolerance = cls._dimension_tolerance(dimension)
            datums = ",".join(dimension.datum_references) or "-"
            critical = " CRITICAL" if dimension.critical else ""
            label = dimension.label or dimension.id
            commands.append(
                cls._text(
                    447,
                    510 - index * 13,
                    7,
                    f"{label}: {dimension.nominal:g} {dimension.unit} {tolerance} "
                    f"DATUM {datums}{critical}",
                )
            )
        if len(dimensions) > 6:
            commands.append(cls._text(447, 432, 7, f"... {len(dimensions) - 6} MORE DIMENSIONS"))
        return commands

    @classmethod
    def _dimension_tolerance(cls, dimension: ResolvedDimension) -> str:
        tolerance = dimension.tolerance.model_dump(mode="json")
        kind = tolerance.get("kind")
        if kind == "symmetric":
            return f"+/- {tolerance['plus_minus_mm']} mm"
        if kind == "deviation":
            return f"+{tolerance['upper_mm']}/ {tolerance['lower_mm']} mm"
        if kind == "basic":
            return "BASIC"
        return "REF"

    @staticmethod
    def _as_mapping(value: Any | None) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        return {"value": value}

    @classmethod
    def _first_text(cls, source: dict[str, Any], *keys: str, default: str) -> str:
        for key in keys:
            value = source.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, dict):
                for nested_key in ("value", "label", "name", "linear_mm"):
                    if value.get(nested_key) not in (None, ""):
                        return cls._ascii(str(value[nested_key]))[:180]
            elif isinstance(value, list):
                return ", ".join(cls._ascii(str(item)) for item in value[:8])[:180]
            else:
                scalar = getattr(value, "value", value)
                return cls._ascii(str(scalar))[:180]
        return default

    @classmethod
    def _joined_field(cls, source: dict[str, Any], *keys: str, default: str) -> str:
        for key in keys:
            value = source.get(key)
            if isinstance(value, list) and value:
                labels = []
                for item in value[:8]:
                    row = cls._as_mapping(item)
                    labels.append(
                        cls._first_text(
                            row,
                            "id",
                            "identifier",
                            "name",
                            "label",
                            "datum",
                            default=str(item),
                        )
                    )
                return ", ".join(labels)[:180]
            if value not in (None, ""):
                return cls._first_text(source, key, default=default)
        return default

    @staticmethod
    def _list_field(source: dict[str, Any], *keys: str) -> list[Any]:
        for key in keys:
            value = source.get(key)
            if isinstance(value, list):
                return value
        return []

    @classmethod
    def _surface_finish(cls, spec: dict[str, Any]) -> str:
        value = spec.get("surface_finish", spec.get("surface_finish_ra", spec.get("ra_um")))
        if isinstance(value, dict):
            value = value.get("ra_um", value.get("value", value.get("roughness")))
        if value in (None, ""):
            finishes = cls._list_field(spec, "surface_finishes")
            if finishes:
                first = cls._as_mapping(finishes[0])
                value = first.get(
                    "ra_micrometers",
                    first.get("ra_um", first.get("value", first.get("roughness"))),
                )
        if value in (None, ""):
            return "Ra 3.2 um"
        text = cls._ascii(str(getattr(value, "value", value)))
        return text if "ra" in text.lower() else f"Ra {text} um"

    @classmethod
    def _general_tolerance(cls, spec: dict[str, Any]) -> str:
        value = spec.get(
            "general_tolerance",
            spec.get("general_tolerance_mm", spec.get("linear_tolerance")),
        )
        if isinstance(value, dict):
            kind = value.get("kind")
            standard = value.get("standard")
            linear = value.get(
                "linear_mm",
                value.get("linear_tolerance_mm", value.get("value")),
            )
            angular = value.get("angular_deg")
            note = value.get("note")
            if kind == "unspecified":
                suffix = f" - {cls._ascii(str(note))}" if note else ""
                return f"UNSPECIFIED{suffix}"[:180]
            if standard and linear not in (None, ""):
                return f"{cls._ascii(str(standard))} / +/- {linear} mm"[:180]
            if standard:
                return cls._ascii(str(standard))[:180]
            if linear not in (None, "") and angular not in (None, ""):
                return f"+/- {linear} mm / +/- {angular} deg"[:180]
            value = linear
        if value in (None, ""):
            return "+/- 0.10 mm"
        text = cls._ascii(str(getattr(value, "value", value)))
        if any(unit in text.lower() for unit in ("mm", "iso", "+/-")):
            return text[:180]
        return f"+/- {text} mm"[:180]

    @classmethod
    def _review_lines(cls, review_summary: Any | None) -> list[str]:
        if review_summary is None:
            return ["REVIEW: NOT YET RECORDED"]
        if isinstance(review_summary, str):
            return [cls._ascii(review_summary)[:180]]
        review = cls._as_mapping(review_summary)
        lines = []
        if review.get("version") not in (None, ""):
            lines.append(f"REVIEW VERSION: {cls._ascii(str(review['version']))}")
        for key, label in (
            ("reviewed_by", "REVIEWED BY"),
            ("reviewer", "REVIEWER"),
            ("reviewed_at", "REVIEWED AT"),
            ("updated_at", "UPDATED AT"),
            ("notes", "NOTES"),
            ("comment", "COMMENT"),
        ):
            if key in review and review[key] not in (None, ""):
                lines.append(f"{label}: {cls._ascii(str(review[key]))}")
        event = review.get("event")
        if event is not None:
            event_row = cls._as_mapping(event)
            actor = cls._first_text(
                event_row,
                "actor",
                "reviewer",
                "reviewed_by",
                default="-",
            )
            transition = cls._first_text(
                event_row,
                "to_status",
                "status",
                "action",
                default="-",
            )
            note = cls._first_text(event_row, "note", "notes", "comment", default="-")
            lines.extend(
                [
                    f"LATEST REVIEWER: {actor}",
                    f"LATEST TRANSITION: {transition}",
                    f"LATEST NOTE: {note}",
                ]
            )
        signature_notice = review.get("signature_notice")
        if signature_notice not in (None, ""):
            lines.append(f"NOTICE: {cls._ascii(str(signature_notice))}")
        checks = review.get("checks")
        if isinstance(checks, list):
            lines.extend(f"CHECK: {cls._ascii(str(check))}" for check in checks[:3])
        return lines or ["REVIEW METADATA PRESENT"]

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
        elif isinstance(base, ProfileRevolutionBase):
            min_radius, _, max_radius, _ = loop_bounds(base.outer)
            commands.append(self._circle(cx, cy, max_radius * scale))
            if min_radius > 1e-7:
                commands.extend(
                    [
                        "[3 2] 0 d",
                        self._circle(cx, cy, min_radius * scale),
                        "[] 0 d",
                    ]
                )
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
        commands = [self._text(left, bottom + height + 16, 9, "FRONT VIEW")]
        if isinstance(doc.base, ProfileRevolutionBase):
            commands.extend(self._revolution_elevation(doc.base, cx, bottom, scale))
        else:
            commands.append(self._rect(left, bottom, width, height))
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
        commands = [self._text(left, bottom + height + 16, 9, "RIGHT VIEW")]
        if isinstance(doc.base, ProfileRevolutionBase):
            commands.extend(self._revolution_elevation(doc.base, cx, bottom, scale))
        else:
            commands.append(self._rect(left, bottom, width, height))
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
    def _revolution_elevation(
        base: ProfileRevolutionBase,
        cx: float,
        bottom: float,
        scale: float,
    ) -> list[str]:
        """Draw the authored radius/Z section on both sides of the global Z axis."""

        _, min_z, _, _ = loop_bounds(base.outer)
        profile = loop_polyline(base.outer)
        commands = [
            EngineeringDrawingPdf._polyline(
                [(cx + radius * scale, bottom + (z - min_z) * scale) for radius, z in profile],
                close=True,
            ),
            EngineeringDrawingPdf._polyline(
                [(cx - radius * scale, bottom + (z - min_z) * scale) for radius, z in profile],
                close=True,
            ),
            "[4 3] 0 d",
            EngineeringDrawingPdf._line(cx, bottom - 5, cx, bottom + (max(z for _, z in profile) - min_z) * scale + 5),
            "[] 0 d",
        ]
        return commands

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
        if isinstance(base, ProfileRevolutionBase):
            _, min_z, max_radius, max_z = loop_bounds(base.outer)
            diameter = 2 * max_radius
            return diameter, diameter, max_z - min_z
        return base.length, base.width, base.height

    @staticmethod
    def _pdf(content: bytes, title: str) -> bytes:
        return EngineeringDrawingPdf._pdf_pages([content], title)

    @staticmethod
    def _pdf_pages(contents: list[bytes], title: str) -> bytes:
        if not contents:
            raise ValueError("at least one PDF page is required")
        if len(contents) > 8:
            raise ValueError("drawing PDF is limited to eight pages")

        page_ids = [3 + index * 2 for index in range(len(contents))]
        font_id = 3 + len(contents) * 2
        info_id = font_id + 1
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            (
                b"<< /Type /Pages /Kids ["
                + b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
                + b"] /Count "
                + str(len(contents)).encode("ascii")
                + b" >>"
            ),
        ]
        for index, content in enumerate(contents):
            if len(content) > 2_000_000:
                raise ValueError("drawing PDF page content exceeds the bounded limit")
            content_id = page_ids[index] + 1
            objects.extend(
                [
                    (
                        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 841.89 595.28] "
                        b"/Resources << /Font << /F1 "
                        + str(font_id).encode("ascii")
                        + b" 0 R >> >> /Contents "
                        + str(content_id).encode("ascii")
                        + b" 0 R >>"
                    ),
                    (
                        b"<< /Length "
                        + str(len(content)).encode("ascii")
                        + b" >>\nstream\n"
                        + content
                        + b"\nendstream"
                    ),
                ]
            )
        objects.extend(
            [
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                (
                    b"<< /Title ("
                    + EngineeringDrawingPdf._ascii(title)
                    .replace("\\", "\\\\")
                    .replace("(", "\\(")
                    .replace(")", "\\)")
                    .encode("ascii")
                    + b") /Creator (PromptCAD Studio) >>"
                ),
            ]
        )
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
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n"
            ).encode("ascii")
        )
        return bytes(output)
