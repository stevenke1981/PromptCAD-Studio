from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MotorFaceStandard:
    key: str
    revision: str
    face_size: float
    mounting_pitch: float
    mounting_thread: str
    mounting_clearance: float
    pilot_diameter: float
    pilot_clearance: float
    shaft_diameter: float
    source_label: str
    source_url: str


NEMA17_FACE = MotorFaceStandard(
    key="nema17-face",
    revision="2026-07-nanotec-st4118",
    face_size=42.3,
    mounting_pitch=31.0,
    mounting_thread="M3",
    mounting_clearance=3.4,
    pilot_diameter=22.0,
    pilot_clearance=22.5,
    shaft_diameter=5.0,
    source_label="Nanotec ST4118 NEMA 17 product overview mechanical drawing",
    source_url="https://www.nanotec.com/fileadmin/files/Baureihenuebersichten/Schrittmotoren/Product_Overview_ST4118.pdf",
)
