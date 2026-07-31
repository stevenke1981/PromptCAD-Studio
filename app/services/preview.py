from __future__ import annotations

import html
from pathlib import Path

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
from app.services.profile_geometry import loop_bounds, loop_polyline


class SvgPreview:
    def write(self, doc: CadDocument, path: Path) -> None:
        path.write_text(self.render(doc), encoding="utf-8")

    def render(self, doc: CadDocument) -> str:
        width, height = 900, 650
        cx, cy = 450, 325
        b = doc.base
        dim_x, dim_y = self._xy_size(b)
        scale = min(600 / max(dim_x, 1), 420 / max(dim_y, 1))
        shapes: list[str] = []
        origin_x, origin_y = 0.0, 0.0

        view_label = "Top preview"
        if isinstance(b, ProfileExtrusionBase):
            min_x, min_y, max_x, max_y = loop_bounds(b.outer)
            origin_x, origin_y = (min_x + max_x) / 2, (min_y + max_y) / 2
            points = loop_polyline(b.outer)
            path = " ".join(
                f"{'M' if index == 0 else 'L'} {cx + (x - origin_x) * scale:.2f} "
                f"{cy - (y - origin_y) * scale:.2f}"
                for index, (x, y) in enumerate(points)
            )
            shapes.append(f'<path d="{path} Z" class="solid"/>')
        elif isinstance(b, ProfileRevolutionBase):
            _, min_z, max_radius, max_z = loop_bounds(b.outer)
            origin_y = (min_z + max_z) / 2
            points = loop_polyline(b.outer)
            for direction in (1, -1):
                path = " ".join(
                    f"{'M' if index == 0 else 'L'} "
                    f"{cx + direction * radius * scale:.2f} "
                    f"{cy - (z - origin_y) * scale:.2f}"
                    for index, (radius, z) in enumerate(points)
                )
                shapes.append(f'<path d="{path} Z" class="solid"/>')
            axis_top = cy - (max_z - origin_y) * scale - 8
            axis_bottom = cy - (min_z - origin_y) * scale + 8
            shapes.append(
                f'<line x1="{cx}" y1="{axis_top:.2f}" x2="{cx}" '
                f'y2="{axis_bottom:.2f}" class="axis"/>'
            )
            view_label = "Front revolution silhouette"
        elif isinstance(b, (PlateBase, EnclosureBase, LBracketBase)):
            w, h = dim_x * scale, dim_y * scale
            x, y = cx - w / 2, cy - h / 2
            shapes.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="4" class="solid"/>')
            if isinstance(b, EnclosureBase):
                inset = b.wall_thickness * scale
                shapes.append(f'<rect x="{x+inset:.2f}" y="{y+inset:.2f}" width="{w-2*inset:.2f}" height="{h-2*inset:.2f}" class="hidden"/>')
            if isinstance(b, LBracketBase):
                t = b.thickness * scale
                shapes.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{t:.2f}" class="accent"/>')
        elif isinstance(b, CylinderBase):
            r = b.diameter * scale / 2
            shapes.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" class="solid"/>')
        elif isinstance(b, RingBase):
            ro = b.outer_diameter * scale / 2
            ri = b.inner_diameter * scale / 2
            shapes.append(f'<circle cx="{cx}" cy="{cy}" r="{ro:.2f}" class="solid"/>')
            shapes.append(f'<circle cx="{cx}" cy="{cy}" r="{ri:.2f}" class="cut"/>')

        for hole in doc.holes:
            if hole.axis.value != "z":
                continue
            x = cx + (hole.x - origin_x) * scale
            y = cy - (hole.y - origin_y) * scale
            r = hole.diameter * scale / 2
            shapes.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{max(r,2):.2f}" class="cut"/>')
            shapes.append(f'<line x1="{x-8:.2f}" y1="{y:.2f}" x2="{x+8:.2f}" y2="{y:.2f}" class="center"/>')
            shapes.append(f'<line x1="{x:.2f}" y1="{y-8:.2f}" x2="{x:.2f}" y2="{y+8:.2f}" class="center"/>')

        if isinstance(b, EnclosureBase):
            wall = max(b.wall_thickness * scale, 4)
            left = cx - dim_x * scale / 2
            top = cy - dim_y * scale / 2
            right = cx + dim_x * scale / 2
            bottom = cy + dim_y * scale / 2
            for cutout in doc.cutouts:
                span = cutout.width * scale
                if cutout.face == SideFace.POSITIVE_Y:
                    rect = cx + cutout.x * scale - span / 2, top, span, wall
                elif cutout.face == SideFace.NEGATIVE_Y:
                    rect = cx + cutout.x * scale - span / 2, bottom - wall, span, wall
                elif cutout.face == SideFace.POSITIVE_X:
                    rect = right - wall, cy - cutout.y * scale - span / 2, wall, span
                else:
                    rect = left, cy - cutout.y * scale - span / 2, wall, span
                shapes.append(
                    f'<rect x="{rect[0]:.2f}" y="{rect[1]:.2f}" '
                    f'width="{rect[2]:.2f}" height="{rect[3]:.2f}" class="cut"/>'
                )

        dimensions = f"{dim_x:g} × {dim_y:g} mm"
        title = html.escape(doc.name)
        material = html.escape(doc.material.value if doc.material else "unspecified")
        subtitle = f"{view_label} · {dimensions} · material: {material}"
        review = "REVIEW REQUIRED" if doc.planner.review_required or doc.assumptions else "PARAMETRIC DRAFT"

        return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
<style>
  .bg {{ fill:#0b1020; }} .grid {{ stroke:#18213a; stroke-width:1; }}
  .solid {{ fill:#26324f; stroke:#8fb4ff; stroke-width:2; }}
  .accent {{ fill:#3e5f93; stroke:#a9c5ff; stroke-width:1.5; }}
  .hidden {{ fill:#0b1020; stroke:#64748b; stroke-dasharray:7 6; stroke-width:1.5; }}
  .cut {{ fill:#0b1020; stroke:#f0b36a; stroke-width:2; }}
  .center {{ stroke:#f0b36a; stroke-width:1; stroke-dasharray:4 3; }}
  .axis {{ stroke:#a9c5ff; stroke-width:1; stroke-dasharray:8 5; }}
  .title {{ fill:#eef4ff; font:700 28px system-ui,sans-serif; }}
  .sub {{ fill:#9fb0cd; font:16px system-ui,sans-serif; }}
  .badge {{ fill:#d7e5ff; font:700 13px system-ui,sans-serif; letter-spacing:1.2px; }}
</style>
<rect width="100%" height="100%" class="bg"/>
<g opacity=".55">{self._grid(width, height)}</g>
<text x="36" y="48" class="title">{title}</text>
<text x="36" y="76" class="sub">{html.escape(subtitle)}</text>
<text x="36" y="618" class="badge">{review}</text>
{''.join(shapes)}
</svg>"""

    @staticmethod
    def _grid(width: int, height: int) -> str:
        lines = []
        for x in range(0, width + 1, 50):
            lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{height}" class="grid"/>')
        for y in range(0, height + 1, 50):
            lines.append(f'<line x1="0" y1="{y}" x2="{width}" y2="{y}" class="grid"/>')
        return "".join(lines)

    @staticmethod
    def _xy_size(base) -> tuple[float, float]:
        if isinstance(base, (PlateBase, EnclosureBase)):
            return base.length, base.width
        if isinstance(base, LBracketBase):
            return base.width, base.depth
        if isinstance(base, CylinderBase):
            return base.diameter, base.diameter
        if isinstance(base, RingBase):
            return base.outer_diameter, base.outer_diameter
        if isinstance(base, ProfileExtrusionBase):
            min_x, min_y, max_x, max_y = loop_bounds(base.outer)
            return max_x - min_x, max_y - min_y
        if isinstance(base, ProfileRevolutionBase):
            _, min_z, max_radius, max_z = loop_bounds(base.outer)
            return 2 * max_radius, max_z - min_z
        return 100, 100
