# DXF-to-CAD acceptance example

Input: `examples/dxf-to-cad/plate-line-arc-four-holes-mm.dxf`

- Source units: millimetres
- Outer profile: two lines and two exact three-point arcs
- Holes: four Ø5 mm through holes
- Extrusion: 6 mm
- Review: required before generation

Observed CadQuery STEP result:

- one valid solid
- bounding box 120 × 40 × 6 mm
- cylinder radii: 20, 20, 2.5, 2.5, 2.5, 2.5 mm

The two 20 mm cylinders are the analytic capsule end arcs; the four 2.5 mm cylinders are the through holes. `web-acceptance.png` records the real browser flow after confirmation.
