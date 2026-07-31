# ADR-002: Parse DXF into normalized CAD DSL

Status: accepted for CHANGE-005

## Context

Directly extruding an uploaded DXF through a CAD kernel would bypass PromptCAD's
typed Feature Tree, unit handling, validation gate, provenance, and review flow.
DXF is untrusted input and can also contain blocks, external references, 3D
entities, unsupported curves, or ambiguous closed regions.

## Decision

PromptCAD parses a bounded DXF in a one-shot worker process using `ezdxf`, then
normalizes only an explicit modelspace allowlist into analytic line/arc segments
and circular holes. The normalized result becomes a typed editable Feature Tree
and `CadDocument 1.1`; existing validators and deterministic compilers remain the
only path to a CAD engine.

The first completed Phase 4 contract accepts one planar closed outer profile,
zero or more circular through holes, explicit extrusion thickness, and declared
or overridden units. Unsupported geometry and ambiguity fail closed.

## Consequences

- Original DXF bytes and filenames are not persisted in jobs or bundles.
- CadQuery receives exact line and three-point arc semantics.
- OpenSCAD, SVG preview, and drawing PDF use a bounded curve approximation.
- Rotation, arrays, dimension-text interpretation, PDF multi-view matching, and
  multiple solid regions require later typed operations rather than parser
  shortcuts.
- The parser dependency is direct and locked; analysis provenance records the
  parser and normalization versions.
