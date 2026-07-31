"""Shared geometry helpers for schema 1.1 profile extrusions.

The authored representation remains exact (lines and three-point arcs).  This
module only tessellates arcs where a polygon is required for validation or a
fallback renderer.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from app.models.cad import ArcSegment2D, LineSegment2D, Point2D, ProfileLoop2D

EPSILON = 1e-7
ARC_MAX_CHORD_MM = 0.5
ARC_MAX_ANGLE_RAD = math.radians(5)
ARC_MAX_SEGMENTS = 96
MAX_VALIDATION_EDGES = 2_048

Point = tuple[float, float]


def point_tuple(point: Point2D) -> Point:
    return point.x, point.y


def points_close(first: Point, second: Point, tolerance: float = EPSILON) -> bool:
    return math.hypot(first[0] - second[0], first[1] - second[1]) <= tolerance


def segment_start(segment: LineSegment2D | ArcSegment2D) -> Point:
    return point_tuple(segment.start)


def segment_end(segment: LineSegment2D | ArcSegment2D) -> Point:
    return point_tuple(segment.end)


def arc_center_and_sweep(arc: ArcSegment2D) -> tuple[Point, float, float, float]:
    """Return center, radius, start angle and signed sweep for an arc."""

    start, mid, end = point_tuple(arc.start), point_tuple(arc.mid), point_tuple(arc.end)
    determinant = 2 * (
        start[0] * (mid[1] - end[1])
        + mid[0] * (end[1] - start[1])
        + end[0] * (start[1] - mid[1])
    )
    if abs(determinant) <= EPSILON or points_close(start, end):
        raise ValueError("arc points must be distinct and non-collinear")

    start_sq = start[0] ** 2 + start[1] ** 2
    mid_sq = mid[0] ** 2 + mid[1] ** 2
    end_sq = end[0] ** 2 + end[1] ** 2
    center = (
        (start_sq * (mid[1] - end[1]) + mid_sq * (end[1] - start[1]) + end_sq * (start[1] - mid[1]))
        / determinant,
        (start_sq * (end[0] - mid[0]) + mid_sq * (start[0] - end[0]) + end_sq * (mid[0] - start[0]))
        / determinant,
    )
    radius = math.dist(center, start)
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    mid_angle = math.atan2(mid[1] - center[1], mid[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    ccw_end = (end_angle - start_angle) % math.tau
    ccw_mid = (mid_angle - start_angle) % math.tau
    sweep = ccw_end if ccw_mid <= ccw_end + EPSILON else ccw_end - math.tau
    if abs(sweep) <= EPSILON:
        raise ValueError("arc sweep must be non-zero")
    return center, radius, start_angle, sweep


def approximate_arc(
    arc: ArcSegment2D,
    max_chord_mm: float = ARC_MAX_CHORD_MM,
) -> list[Point]:
    center, radius, start_angle, sweep = arc_center_and_sweep(arc)
    requested_segments = max(
        1,
        math.ceil(abs(sweep) / ARC_MAX_ANGLE_RAD),
        math.ceil(abs(sweep) * radius / max_chord_mm),
    )
    segments = min(requested_segments, ARC_MAX_SEGMENTS)
    return [
        (
            center[0] + radius * math.cos(start_angle + sweep * index / segments),
            center[1] + radius * math.sin(start_angle + sweep * index / segments),
        )
        for index in range(segments + 1)
    ]


def segment_points(segment: LineSegment2D | ArcSegment2D) -> list[Point]:
    if isinstance(segment, LineSegment2D):
        return [segment_start(segment), segment_end(segment)]
    return approximate_arc(segment)


def loop_polyline(loop: ProfileLoop2D) -> list[Point]:
    points: list[Point] = []
    for segment in loop.segments:
        segment_path = segment_points(segment)
        if not points:
            points.extend(segment_path)
        else:
            points.extend(segment_path[1:])
    return points


def loop_bounds(loop: ProfileLoop2D) -> tuple[float, float, float, float]:
    points: list[Point] = []
    for segment in loop.segments:
        if isinstance(segment, LineSegment2D):
            points.extend((segment_start(segment), segment_end(segment)))
            continue
        center, radius, start_angle, sweep = arc_center_and_sweep(segment)
        points.extend((segment_start(segment), segment_end(segment)))
        for angle in (0.0, math.pi / 2, math.pi, math.pi * 1.5):
            if _angle_in_sweep(angle, start_angle, sweep):
                points.append((center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)))
    if not points:
        raise ValueError("profile loop has no points")
    xs, ys = zip(*points, strict=True)
    return min(xs), min(ys), max(xs), max(ys)


def _angle_in_sweep(angle: float, start_angle: float, sweep: float) -> bool:
    if sweep > 0:
        return (angle - start_angle) % math.tau <= sweep + EPSILON
    return (start_angle - angle) % math.tau <= -sweep + EPSILON


def loop_signed_area(loop: ProfileLoop2D) -> float:
    points = loop_polyline(loop)
    if len(points) < 3:
        return 0.0
    if not points_close(points[0], points[-1]):
        points.append(points[0])
    return sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:], strict=False)
    ) / 2


def point_on_segment(point: Point, start: Point, end: Point) -> bool:
    cross = (point[1] - start[1]) * (end[0] - start[0]) - (point[0] - start[0]) * (end[1] - start[1])
    if abs(cross) > EPSILON:
        return False
    return (
        min(start[0], end[0]) - EPSILON <= point[0] <= max(start[0], end[0]) + EPSILON
        and min(start[1], end[1]) - EPSILON <= point[1] <= max(start[1], end[1]) + EPSILON
    )


def point_in_loop(point: Point, loop: ProfileLoop2D) -> bool:
    """Return whether a point lies inside or on a tessellated profile loop."""

    points = loop_polyline(loop)
    if len(points) < 3:
        return False
    if not points_close(points[0], points[-1]):
        points.append(points[0])
    inside = False
    for start, end in zip(points, points[1:], strict=False):
        if point_on_segment(point, start, end):
            return True
        if (start[1] > point[1]) != (end[1] > point[1]):
            x_crossing = (end[0] - start[0]) * (point[1] - start[1]) / (end[1] - start[1]) + start[0]
            if point[0] < x_crossing:
                inside = not inside
    return inside


def circle_in_loop(center: Point, radius: float, loop: ProfileLoop2D) -> bool:
    return all(
        point_in_loop(
            (
                center[0] + radius * math.cos(math.tau * index / 32),
                center[1] + radius * math.sin(math.tau * index / 32),
            ),
            loop,
        )
        for index in range(32)
    )


def _orientation(first: Point, second: Point, third: Point) -> int:
    cross = (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])
    if abs(cross) <= EPSILON:
        return 0
    return 1 if cross > 0 else -1


def segments_intersect(first_start: Point, first_end: Point, second_start: Point, second_end: Point) -> bool:
    first = _orientation(first_start, first_end, second_start)
    second = _orientation(first_start, first_end, second_end)
    third = _orientation(second_start, second_end, first_start)
    fourth = _orientation(second_start, second_end, first_end)
    if first != second and third != fourth:
        return True
    return (
        (first == 0 and point_on_segment(second_start, first_start, first_end))
        or (second == 0 and point_on_segment(second_end, first_start, first_end))
        or (third == 0 and point_on_segment(first_start, second_start, second_end))
        or (fourth == 0 and point_on_segment(first_end, second_start, second_end))
    )


def loop_self_intersects(loop: ProfileLoop2D) -> bool:
    points = loop_polyline(loop)
    edges = list(zip(points, points[1:], strict=False))
    for index, (first_start, first_end) in enumerate(edges):
        for second_start, second_end in edges[index + 1 :]:
            if any(
                points_close(first, second)
                for first, second in (
                    (first_start, second_start),
                    (first_start, second_end),
                    (first_end, second_start),
                    (first_end, second_end),
                )
            ):
                continue
            if segments_intersect(first_start, first_end, second_start, second_end):
                return True
    return False


def is_degenerate(segment: LineSegment2D | ArcSegment2D) -> bool:
    if isinstance(segment, LineSegment2D):
        return points_close(segment_start(segment), segment_end(segment))
    try:
        arc_center_and_sweep(segment)
    except ValueError:
        return True
    return False


def translated_points(points: Iterable[Point], center: Point, scale: float, invert_y: bool = False) -> list[Point]:
    sign = -1 if invert_y else 1
    return [
        (center[0] + point[0] * scale, center[1] + sign * point[1] * scale)
        for point in points
    ]
