# ADR-006: Bounded DXF revolution and pattern semantics

Date: 2026-07-31

Status: Accepted

## Context

The first DXF slice always extruded one closed loop and flattened every CIRCLE into an individual hole. That could not represent a rotational half-profile, preserve authored array intent, or distinguish an exact rounded or chamfered outline from a reusable manufacturing feature. General engineering-drawing interpretation remains ambiguous, especially with multiple views and local edge treatments.

## Decision

- Introduce `CadDocument 1.2 profile_revolution` as a closed radius/Z profile revolved 360 degrees around global Z.
- Treat exactly one horizontal or vertical LINE on a CENTER layer or linetype as the only executable revolution-axis signal. Auto mode uses it; explicit mode can force extrusion or require revolution.
- Require the half-profile to touch the axis, stay on one side, and pass the existing closed-loop and geometry validation gates.
- Preserve linear and circular hole arrays in the DXF Feature Tree, but expand them into bounded existing `HoleFeature` records at the canonical DSL boundary. This avoids changing every backend's feature semantics.
- Infer fillet or chamfer only when all four corners of an axis-aligned rectangle have the same authored treatment and can be reconstructed as a sharp rectangle plus the existing global vertical selector.
- Reject revolution holes, cutouts, and top-level finishing features in schema 1.2 until their 3D coordinate semantics are explicitly modeled.

## Consequences

DXF can now produce editable extrusion or revolution intent without allowing arbitrary code or silently choosing a sloped or crossing axis. Existing backends receive either one new bounded base kind or the same explicit hole and finishing features they already understand. Multi-view association, dimension annotations, partial arrays, and local edge selectors remain separate future decisions rather than unsafe heuristics in this slice.
