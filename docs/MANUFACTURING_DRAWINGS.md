# Manufacturing Package MVP

PromptCAD Studio keeps solid geometry and manufacturing intent in separate, versioned contracts:

- `spec.json` is the immutable `CadDocument` geometry used by CAD compilers.
- `drawing-spec.json` is `ManufacturingDrawingSpec 1.0`: dimensions, tolerances, datums, surface finish, BOM, title block, and revision history.
- `review.json` is the hash-bound workflow envelope and append-only event history.

This avoids changing every CAD backend when only drawing metadata or approval state changes.

## Supported drawing intent

The first manufacturing slice supports one bounded part and finite schedules:

- overall X/Y/Z and typed feature dimensions;
- symmetric, bilateral, or limit tolerances;
- datum labels bound to supported faces or axes;
- Ra surface-finish requirements;
- a finite single-level BOM;
- drawing number, title, sheet, projection, revision, author, and issue date;
- revision history and workflow status.

Nominal dimension values are resolved from the accepted `CadDocument`. The drawing contract does not accept a second arbitrary nominal value that could disagree with the actual CAD geometry.

## Review lifecycle

```text
draft → in_review → approved
                  └→ rejected
```

- `expected_version` prevents stale browser or CLI actions from overwriting a newer transition.
- `approved` and `rejected` are terminal for a job.
- Every transition rechecks the SHA-256 hashes of the geometry, drawing spec, and original manufacturing PDF.
- Review PDFs use additive filenames; prior PDFs are never overwritten.
- A geometry or drawing change requires a new job/revision.

The actor field is self-asserted. This workflow provides traceability but is not a cryptographic signature, identity proof, or legal electronic signature.

## Safety boundaries

- Unknown fields and unsupported feature references are rejected.
- Duplicate dimension, datum, BOM, or revision identifiers are rejected.
- A dimension must have its own tolerance or inherit the general tolerance.
- A title-block revision must match the latest revision-history entry.
- Draft work cannot be approved directly; rejection requires a comment.
- Hash mismatch, stale version, invalid transition, or terminal-state mutation fails closed and does not create a derivative PDF.

This MVP does not claim complete ASME Y14.5 or ISO GPS coverage. Full GD&T feature-control frames, arbitrary section/detail views, assemblies, supplier signatures, and PKI-backed signing remain outside this slice.
