# ADR-004: Durable asynchronous CAD worker queue

**Status:** Accepted
**Date:** 2026-07-31

## Context

Native CAD kernels can be slow, consume significant CPU and memory, or crash on degenerate geometry. Synchronous HTTP execution cannot survive a browser disconnect and gives users no durable cancellation or restart recovery. The Phase 5 subprocess controls were defense in depth but kept the kernel in the network-facing service topology.

## Decision

PromptCAD keeps the existing synchronous contract and adds a local durable queue for prompt and edited-spec generation. SQLite WAL is the default single-host store because it has no service dependency, supports transactional atomic claims, and shares the existing persistent `generated/` volume. It is not presented as a multi-node queue.

Queue jobs have a closed kind allowlist, bounded JSON payloads inherited from request limits and DSL schema, five explicit states, timestamps, attempt count, cancellation flag, result job ID, worker lease, and bounded error text. Workers claim through `BEGIN IMMEDIATE`, renew their lease, recover expired claims, and retry only to a configured maximum. Worker payloads are validated again with the public Pydantic request models.

Cancellation is cooperative across JobService boundaries and the renderer loop. A queued job becomes cancelled atomically. A running job sets a durable cancellation flag; the worker observes it and the renderer terminates the complete child process tree. Terminal output continues to use the existing manifest and artifact contract; the queue stores only its result job ID and URL.

The hardened Docker override forces the network-facing API to source-only and runs the CAD worker with no network, a read-only root filesystem, dropped capabilities, no-new-privileges, and CPU, memory, and PID limits. The shared generated volume is the only persistent write boundary.

## Consequences

Positive:

- Browser and CLI clients can disconnect and resume by queue ID.
- API restarts do not lose queued work.
- Cancellation and retry outcomes are explicit rather than inferred from HTTP disconnects.
- The same manifest, download, validation, and backend contracts serve synchronous and asynchronous clients.
- Native CAD execution can be isolated from the network-facing API without a hosted queue dependency.

Trade-offs:

- SQLite is a single-host queue; multi-node operation requires an external transactional queue/database.
- Completed queue rows and generated jobs need an operator retention policy.
- The provided container controls are service-level, not a new container per job.
- LLM planning is disabled in the no-network worker profile; use reviewed DSL or rule/agent planning there.

## Verification

- Atomic-claim, capacity, queued cancellation, lease recovery, worker completion, REST and CLI tests cover the durable path.
- Full Phase 1 through Phase 5 regression remains required before Baseline acceptance.
- Browser acceptance must demonstrate submit-to-result and queued cancellation.
- Docker Compose startup remains host-dependent and must be accepted on a Docker-enabled machine.
