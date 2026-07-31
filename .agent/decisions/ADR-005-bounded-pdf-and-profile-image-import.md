# ADR-005: Bounded PDF rasterization and polygonal image profiles

Date: 2026-07-31

Status: Accepted

## Context

The Phase 3 image path accepted only calibrated PNG/JPEG rectangles and circular holes. The requested product scope also includes scans, patent drawings, PDF pages, perspective photographs, and non-rectangular closed parts. Treating every convex quadrilateral as a free profile would silently convert a perspective photograph into incorrect manufacturing geometry, while parsing unbounded PDF input adds a native-runtime denial-of-service boundary.

## Decision

- Detect PDF by `%PDF-` content, not filename or MIME, and rasterize only an explicitly selected page.
- Bound compressed bytes, total page count, page index, rendered dimension, and rendered pixel count before contour analysis.
- Serialize PDFium document open, render, and native handle teardown under one process-wide lock.
- Keep perspective correction opt-in and limited to one convex four-corner candidate. Without that opt-in, an ambiguous convex non-rectangular quadrilateral fails closed.
- Represent a reliable non-rectangular closed contour as a finite, editable polygon node and compile it to `CadDocument 1.1 profile_extrusion`.
- Require unique points, non-zero signed area, typed parent relationships, source provenance, human review, and the existing design validation gate.

## Consequences

PDF, scan, sketch, and simple perspective-photo workflows now share the same reviewed Feature Tree and CAD pipeline as PNG/JPEG. Scale and thickness remain explicit because a single unreferenced view cannot supply trustworthy manufacturing dimensions. Raster profiles are polygonal rather than analytic arcs, and the in-process PDF native boundary remains a documented limitation for future one-shot parser isolation.
