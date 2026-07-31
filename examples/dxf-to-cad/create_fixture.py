from __future__ import annotations

from pathlib import Path

import ezdxf


def main() -> None:
    """Create millimetre DXF fixtures accepted by the restricted importer."""
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4  # millimetres
    modelspace = document.modelspace()
    for start, end in [((0, 0), (80, 0)), ((80, 0), (80, 40)), ((80, 40), (0, 40)), ((0, 40), (0, 0))]:
        modelspace.add_line(start, end)
    modelspace.add_circle((20, 20), radius=3)
    modelspace.add_circle((60, 20), radius=3)
    output = Path(__file__).with_name("plate-two-holes-mm.dxf")
    document.saveas(output)
    print(output)

    capsule = ezdxf.new("R2010")
    capsule.header["$INSUNITS"] = 4
    modelspace = capsule.modelspace()
    modelspace.add_line((10, 0), (90, 0))
    modelspace.add_arc((90, 20), radius=20, start_angle=270, end_angle=90)
    modelspace.add_line((90, 40), (10, 40))
    modelspace.add_arc((10, 20), radius=20, start_angle=90, end_angle=270)
    for center in ((20, 15), (40, 25), (60, 15), (80, 25)):
        modelspace.add_circle(center, radius=2.5)
    capsule_output = Path(__file__).with_name("plate-line-arc-four-holes-mm.dxf")
    capsule.saveas(capsule_output)
    print(capsule_output)


if __name__ == "__main__":
    main()
