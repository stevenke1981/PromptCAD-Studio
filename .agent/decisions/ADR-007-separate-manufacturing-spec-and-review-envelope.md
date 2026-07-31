# ADR-007: Separate manufacturing intent from CAD geometry

Date: 2026-08-01

Status: Accepted

## Context

`CadDocument` is the closed geometry contract shared by every CAD compiler. Manufacturing dimensions, tolerances, datums, surface finish, BOM, revision history, and approval status affect drawings and workflow rather than solid geometry. Adding those fields to the CAD schema would force unrelated backend changes and couple mutable review state to immutable geometry.

## Decision

- Keep `CadDocument` as the geometry contract and introduce a separate strict `ManufacturingDrawingSpec 1.0`.
- Resolve nominal dimension values from the accepted CAD document through typed feature targets; do not accept a second free-form nominal value.
- Bind the drawing spec and review record to SHA-256 hashes of `spec.json`, `drawing-spec.json`, and the original manufacturing PDF.
- Use an explicit `draft → in_review → approved | rejected` state machine with optimistic `expected_version` checks and terminal approved/rejected states.
- Write every review derivative PDF under a new filename. Never overwrite the original drawing or prior review evidence.
- Treat actor names as self-asserted workflow labels and display that approval is not a cryptographic signature.

## Consequences

Existing CAD files and compilers remain compatible, while manufacturing packages gain a versioned, reviewable contract. A design or drawing change after approval requires a new job/revision rather than mutation. The first slice is bounded to one part, finite schedules, and workflow approval; it does not claim full ASME Y14.5 or ISO GPS coverage.
