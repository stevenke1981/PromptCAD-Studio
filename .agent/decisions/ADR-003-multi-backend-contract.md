# ADR-003: Closed multi-backend capability contract

**Status:** Accepted
**Date:** 2026-07-31
**Contract:** CadBackend 1.0

## Context

Phase 1–4 established one validated `CadDocument` DSL, CadQuery and OpenSCAD generation, and reviewed image/DXF ingestion. Adding Build123d, FreeCAD, Fusion 360, and SOLIDWORKS creates two risks: backend-specific semantics can silently diverge, and request-driven plugin or executable selection can become code execution.

Desktop CAD products also have licensing and host-application constraints that are incompatible with unattended server execution. CadQuery 2.8.0 and Build123d 0.11.1 depend on conflicting OCP distributions and cannot safely share one Python environment.

## Decision

PromptCAD uses a closed, server-owned `BackendRegistry`. Contract 1.0 exposes a fixed backend ID, compiler and contract versions, execution kind, source availability, runtime state, supported schemas/features/formats, source filename, and semantic fidelity.

Requests may select only:

`auto`, `cadquery`, `build123d`, `freecad`, `openscad`, `fusion360`, `solidworks`, or `source_only`.

They cannot provide import paths, executables, arguments, environments, class names, or plugin metadata. The registry imports its compiler implementations statically.

All six sources are generated deterministically from the schema-validated DSL. Prompt text is embedded only as JSON data. The shared `DesignValidator` remains the execution gate: an error prevents every CAD runner.

Execution policy:

- `auto` is CadQuery → OpenSCAD → source-only.
- CadQuery and OpenSCAD preserve their existing local-process behavior.
- Build123d runs only when explicitly selected and installed in a Build123d-specific environment.
- FreeCAD produces source only and is never executed by the server.
- Fusion 360 and SOLIDWORKS produce host-application adapters that import the sibling `model.step`; the server never executes them.
- OpenSCAD fillet/chamfer semantic loss fails closed before runtime.

CadQuery and Build123d extras must be installed into different virtual environments. Neither runtime is allowed to replace the other silently.

Every job records `backend-report.json`, canonical spec SHA-256, per-source SHA-256, artifact SHA-256, capability snapshot, requested/selected/effective backend, diagnostics, fallback chain, and per-format results.

## Security boundary

Local runners use fixed command arrays with `shell=false`, closed stdin, private staging/HOME/TEMP, an environment allowlist, concurrency and timeout limits, bounded console output, bounded artifacts, path/symlink checks, format signature checks, and atomic promotion.

These controls are defense in depth, not an OS sandbox. Public deployment requires a separate renderer worker with no outbound network, a low-privilege identity, read-only root filesystem, cgroup/seccomp or platform-equivalent isolation, authentication, rate limits, quotas, and retention controls.

## Consequences

Positive:

- API, CLI, and Web share one auditable backend allowlist.
- All backends consume the same units, features, validation, and provenance.
- Source-only environments remain useful without fabricating native CAD artifacts.
- Desktop licensing boundaries are explicit and cannot be crossed by a normal request.
- Backend availability and degradation are machine-readable.

Trade-offs:

- Adding a backend requires a code change, review, conformance fixtures, and a contract update when semantics change.
- Build123d needs its own service or venv because of OCP conflicts.
- Fusion 360 and SOLIDWORKS adapters depend on a neutral STEP bridge and may not preserve native parametric history.
- FreeCAD, Fusion 360, and SOLIDWORKS host behavior remains unaccepted until run in their actual host environments.

## Verification

- 128 Phase 1–5 pytest cases pass with 81% app coverage.
- Six sources compile deterministically against shared CadDocument fixtures; Python outputs parse successfully.
- Unsafe and unknown backend identifiers are rejected.
- Prompt-injection fixtures remain inert JSON data.
- Host adapters contain no subprocess, environment, or PromptCAD internal imports.
- Build123d in a dedicated environment produced a valid 80×40×5 mm STEP with two radius 3.3 mm cylindrical hole faces.
