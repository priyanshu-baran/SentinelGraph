# SentinelGraph
### A Graph-Native Incident Commander

SentinelGraph is a hackathon prototype for graph-backed incident investigation, bounded remediation, independent recovery verification, and reusable incident memory.

**Target tracks**
- Track 1: Agents That Act on Connected Data
- Track 2: Agent Memory & Coordination

FalkorDB is the incident knowledge graph. Prometheus supplies telemetry from the OpenTelemetry Demo. Python tools investigate incidents, execute allowlisted remediation, verify recovery, and retrieve reusable memory.

## Demonstrated incident

We tested a payment failure in the OpenTelemetry Demo:

1. **Fault observed:** Checkout payment RPCs failed with `UNAVAILABLE`. The underlying error showed a connection attempt to `badAddress:50051`.
2. **Graph investigation:** FalkorDB connected the incident to Checkout and its Payment dependency over gRPC.
3. **Bounded remediation:** The allowlisted action `disable_payment_fault` changed the `paymentUnreachable` flag's default variant to `off`.
4. **Service recovery:** Checkout was restarted to pick up the corrected flag configuration. Storefront order placement worked afterward.
5. **Independent verification:** The verifier checked the live flagd evaluation and Prometheus payment RPC telemetry. Three qualifying clean observations, separated by at least 120 seconds, were recorded.
6. **Recovery recorded:** FalkorDB marked `incident-payment-unreachable-001` as `recovered`, with three clean checks and a populated verification timestamp.
7. **Memory retrieval:** A fresh `investigate.py` process retrieved the stored remediation memory. Because the incident was already recovered, it correctly proposed no new action.

Observed final verification: `paymentUnreachable=false`, zero `UNAVAILABLE` calls in the qualifying windows, and incident status `recovered`.

## Architecture

```text
OpenTelemetry Demo
        |
        v
   Prometheus -----> collector.py
                         |
                         v
                      FalkorDB
                         |
                         v
                   investigate.py
                         |
                  allowlist + approval
                         |
                         v
                  remediation.py
                         |
                       flagd
                         |
                         v
                  verify_recovery.py
                         |
                         v
                 recovered incident
                         |
                         v
               reusable graph memory
```

## Implementation

- `sentinelgraph/collector.py` — collects payment RPC telemetry from Prometheus and updates graph evidence/status.
- `sentinelgraph/investigate.py` — queries incident and dependency evidence, retrieves matching reusable remediation memories, validates actions against the allowlist, and requires explicit `APPLY` confirmation before execution.
- `sentinelgraph/remediation.py` — implements the allowlisted `disable_payment_fault` action and supports dry-run validation.
- `sentinelgraph/verify_recovery.py` — checks the live flagd evaluation and recent payment RPC telemetry; requires three qualifying clean checks at least 120 seconds apart before marking recovery.
- `otel-demo/` — OpenTelemetry Demo and its Compose configuration.
- `otel-demo/src/flagd/demo.flagd.json` — demo feature-flag configuration.

## Run the demo

Prerequisites: Docker Compose, Python, and the root `.venv` with `redis` and `requests` installed. Start FalkorDB and the OpenTelemetry Demo first.

From the project root:

```bash
docker compose up -d
docker exec falkordb redis-cli PING

source .venv/bin/activate

python sentinelgraph/collector.py
python sentinelgraph/investigate.py
python sentinelgraph/remediation.py disable_payment_fault
python sentinelgraph/verify_recovery.py
```

The remediation command defaults to a dry run. Applying it requires the explicit `--apply` option and should only be done during a controlled fault exercise.

The recovery verifier's default flagd endpoint may need updating if Docker assigns a different host port. Set `FLAGD_EVALUATION_URL` to the active OFREP endpoint when necessary.

For the demonstrated incident, the verifier was run with:

```bash
FLAGD_EVALUATION_URL=http://localhost:32804/ofrep/v1/evaluate/flags/paymentUnreachable \
python sentinelgraph/verify_recovery.py
```

The port above is specific to the tested environment and may change after a restart.

## Safety and limitations

- Remediation is restricted to an explicit allowlist.
- Investigation uses deterministic graph queries rather than arbitrary model-generated Cypher.
- The `investigate.py` command requires explicit approval before executing remediation.
- Dependency relationships provide context for investigation; they do not alone prove root cause.
- Recovery requires observed telemetry, not merely a successful configuration change.
- The demonstrated memory retrieval uses a new process and reads persistent memory from FalkorDB.
- Memory creation/promotion is not yet a fully automated post-recovery workflow.
- This prototype does not yet implement a complete multi-agent coordination system or unrestricted autonomous operation.
- The demo's fault injection and recovery depend on the OpenTelemetry Demo and flagd configuration.
- Recent telemetry can be empty when no payment requests occur. Generate traffic before running verification.
- The `product-catalog` memory-limit adjustment in `otel-demo/compose.yaml` is a local project change and should be preserved.

## Track alignment

**Track 1 — Agents That Act on Connected Data:** graph-based incident and dependency investigation, telemetry evidence, allowlisted remediation, explicit approval, and independent recovery verification.

**Track 2 — Agent Memory & Coordination:** persistent remediation memory stored in FalkorDB and retrieved by a fresh investigation process, with a safety guard that avoids reapplying remediation to a recovered incident.

The implemented scope is a working prototype; full multi-agent coordination and automated memory promotion remain future work.
