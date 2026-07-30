# Calibrated image-to-CAD acceptance example

Input: `examples/image-to-cad/plate-top-view.png`

Calibration and user-supplied manufacturing input:

- Longest outer edge: 100 mm
- Thickness: 5 mm
- Image: 1000 × 700 PNG, single high-contrast top-view part

Detected result:

- Rectangle: 100 × 60 mm
- Four circular through holes at `(±30, ±10)` mm
- Detected hole diameter: approximately 5.22 mm
- Editable Feature Tree: rectangle sketch, extrusion, four circle sketches, four cuts

Accepted CLI:

```powershell
promptcad image examples/image-to-cad/plate-top-view.png `
  --known-length 100 --thickness 5 `
  --analysis-output examples/generated/image-to-cad/image-analysis.json

promptcad image examples/image-to-cad/plate-top-view.png `
  --known-length 100 --thickness 5 `
  --feature-tree-input examples/generated/image-to-cad/image-analysis.json `
  --confirm
```

The accepted job keeps `planner_used=image-feature-tree`; `image-analysis.json`
and `feature-tree.json` are first-class manifest artifacts. The stored analysis
contains calibration provenance but no raw image, filename, or reusable analysis token.

The generated STEP was imported back as one solid with a `100 × 60 × 5 mm`
bounding box and four cylindrical faces.
