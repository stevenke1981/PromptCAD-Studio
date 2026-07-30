from __future__ import annotations

import re

# ISO-like coarse series used only for planning defaults. The selected diameter is
# always preserved as an explicit CAD DSL value and must be reviewed for production.
METRIC_CLEARANCE_MM: dict[float, float] = {
    2.0: 2.4,
    2.5: 2.9,
    3.0: 3.4,
    4.0: 4.5,
    5.0: 5.5,
    6.0: 6.6,
    8.0: 9.0,
    10.0: 11.0,
    12.0: 13.5,
}

METRIC_TAP_DRILL_MM: dict[float, float] = {
    2.0: 1.6,
    2.5: 2.05,
    3.0: 2.5,
    4.0: 3.3,
    5.0: 4.2,
    6.0: 5.0,
    8.0: 6.8,
    10.0: 8.5,
    12.0: 10.2,
}

_METRIC_THREAD = re.compile(r"^\s*M\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def metric_thread_nominal(thread: str | None) -> float | None:
    if not thread:
        return None
    match = _METRIC_THREAD.match(thread)
    return float(match.group(1)) if match else None


def metric_clearance_diameter(nominal: float) -> tuple[float, bool]:
    value = METRIC_CLEARANCE_MM.get(float(nominal))
    if value is not None:
        return value, True
    return round(float(nominal) * 1.1, 2), False


def metric_tap_drill_diameter(nominal: float) -> tuple[float, bool]:
    value = METRIC_TAP_DRILL_MM.get(float(nominal))
    if value is not None:
        return value, True
    return round(float(nominal) * 0.8, 2), False
