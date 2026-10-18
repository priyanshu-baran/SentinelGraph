# SentinelGraph
### A Graph-Native Autonomous Incident Commander

SentinelGraph is a hackathon prototype that investigates service incidents using a graph of dependencies and persistent remediation memory. It combines FalkorDB, Prometheus telemetry, bounded remediation, and independent recovery verification.

## Target Tracks

- **Track 1 — Agents That Act on Connected Data**
- **Track 2 — Agent Memory & Coordination**

The implemented scope focuses on these two tracks. Full multi-agent coordination and fully automated memory promotion are future work.

## Demonstrated Incident

We tested a payment failure in the OpenTelemetry Demo.

1. **Fault observed:** Checkout payment RPCs failed with `UNAVAILABLE`. The injected fault attempted to connect to `badAddress:50051`.
2. **Graph investigation:** FalkorDB connected the incident to Checkout and its Payment dependency over gRPC.
3. **Bounded remediation:** The allowlisted action `disable_payment_fault` changed the `paymentUnreachable` flag's default variant to `off`.
4. **Service recovery:** Checkout was restarted to pick up the corrected flag configuration. Storefront order placement worked afterward.
5. **Independent verification:** The verifier checked the live flagd evaluation and Prometheus payment RPC telemetry.
6. **Recovery recorded:** Three qualifying clean checks were recorded, and FalkorDB marked `incident-payment-unreachable-001` as `recovered`.
7. **Memory retrieval:** A fresh `investigate.py` process retrieved persistent remediation memory. Since the incident was already recovered, the safety guard prevented another remediation.

**Observed result:** `paymentUnreachable=false`, zero `UNAVAILABLE` calls in the qualifying verification windows, and incident status `recovered`.

## Architecture

```text
             OpenTelemetry Demo
                      |
                      v
                 Prometheus
                      |
                      v
                collector.py
                      |
                      v
                   FalkorDB
                  /        \
                 v          v
         investigate.py   Persistent
                 |        remediation
                 |          memory
                 v
       Allowlist + approval
                 |
                 v
          remediation.py
                 |
                 v
               flagd
                 |
                 v
        Restart Checkout service
                 |
                 v
         verify_recovery.py
                 |
                 v
      Prometheus + live flag check
                 |
                 v
       Incident marked recovered
```

## Graph Data Model

FalkorDB stores incident state, service dependencies, and reusable remediation memory. Prometheus provides the operational telemetry used to investigate failures and verify recovery.

### Nodes

| Node label | Purpose |
|---|---|
| `Incident` | Incident ID, title, status, error type, telemetry evidence, and recovery-verification state |
| `Service` | Service identity, namespace, and criticality where available |
| `RemediationMemory` | Incident type, symptom, previously successful action, target flag and variant, outcome, and reusability metadata |

### Relationships

| Relationship | Meaning |
|---|---|
| `(Incident)-[:AFFECTS]->(Service)` | Connects an incident to the affected service |
| `(Service)-[:DEPENDS_ON]->(Service)` | Represents a service dependency; stores protocol and RPC operation |
| `(Incident)-[:INVOLVES]->(Service)` | Connects an incident to another service involved in the failure |

### Example graph

```text
(Incident: payment unavailable)
       |
       | AFFECTS
       v
(Service: checkout)
       |
       | DEPENDS_ON
       | {protocol: "grpc", operation: "..."}
       v
(Service: payment)

(Incident)
       |
       | INVOLVES
       v
(Service: payment)

(RemediationMemory)
  symptom
  action
  target flag and variant
  outcome
  clean checks and verification metadata
```

### Cypher Queries

The graph is used to retrieve incident state, trace service dependencies, and find relevant remediation memories. The following queries illustrate these operations.

**1. Retrieve incident state**

```cypher
MATCH (i:Incident {id: 'incident-payment-unreachable-001'})
RETURN i.id AS id,
       i.status AS status,
       i.error_type AS error_type,
       i.recent_failed_calls AS recent_failed,
       i.recent_successful_calls AS recent_successful,
       i.failure_share_observed AS failure_share
```

**2. Trace the affected service to its dependency**

```cypher
MATCH (i:Incident {id: 'incident-payment-unreachable-001'})
      -[:AFFECTS]->(affected:Service)
      -[d:DEPENDS_ON]->(dependency:Service)
RETURN affected.name AS affected_service,
       d.protocol AS protocol,
       d.operation AS operation,
       dependency.name AS dependency_service
```

This query connects the incident to Checkout and its Payment dependency. The dependency relationship provides investigation context; it does not alone prove root cause.

**3. Retrieve reusable remediation memory**

```cypher
MATCH (m:RemediationMemory)
WHERE m.incident_type = $incident_type
  AND m.reusable = true
  AND m.outcome = 'recovered'
RETURN m.id AS id,
       m.symptom AS symptom,
       m.action AS action,
       m.flag AS flag,
       m.target_variant AS target_variant
```

The investigation uses matching persistent memory and validates proposed actions against the remediation allowlist. A recovered incident is not remediated again.

**4. Record and verify recovery**

The collector updates graph evidence from Prometheus telemetry. The recovery verifier checks the live flagd evaluation and recent payment RPC telemetry, then records clean-check progress in the incident graph. The demonstrated workflow requires three qualifying clean checks before marking the incident recovered.

## Implementation

- `sentinelgraph/collector.py` — collects payment RPC telemetry from Prometheus and updates graph evidence and incident state.
- `sentinelgraph/investigate.py` — queries incident and dependency evidence, retrieves reusable remediation memories, validates proposed actions, and requires explicit `APPLY` confirmation before executing remediation.
- `sentinelgraph/remediation.py` — implements the allowlisted `disable_payment_fault` action and supports dry-run validation.
- `sentinelgraph/verify_recovery.py` — checks the live flagd evaluation and payment RPC telemetry, requiring three qualifying clean checks at least 120 seconds apart before marking recovery.
- `docker-compose.yml` — starts FalkorDB for the project.
- `otel-demo-local.patch` — preserves the local OpenTelemetry Demo configuration changes used in the demonstration.

## Run the Demo

### Prerequisites

- Docker Engine and Docker Compose
- Python 3
- Git
- The Python packages `redis` and `requests`

### 1. Start FalkorDB

From the project root:

```bash
docker compose up -d
docker exec falkordb redis-cli PING
```

A successful Redis health check should return `PONG`.

### 2. Set up the OpenTelemetry Demo

The upstream OpenTelemetry Demo source is not vendored into this repository. Clone it separately:

```bash
git clone https://github.com/open-telemetry/opentelemetry-demo.git otel-demo
git -C otel-demo apply ../otel-demo-local.patch
```

The patch preserves the local configuration changes used for this demonstration. If the upstream repository has changed and the patch no longer applies, use the compatible OpenTelemetry Demo revision for the patch.

Start the minimal demo:

```bash
cd otel-demo
make start-minimal
cd ..
```

Confirm that Prometheus and the demo services are running before proceeding.

### 3. Prepare the Python environment

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install redis requests
```

### 4. Collect telemetry and investigate

```bash
python sentinelgraph/collector.py
python sentinelgraph/investigate.py
```

The investigation shows incident status, graph dependency evidence, and any matching remediation memories.

### 5. Exercise remediation in a controlled test

The remediation command defaults to a dry run. Review its output first:

```bash
python sentinelgraph/remediation.py disable_payment_fault
```

Apply the action only during a controlled fault exercise:

```bash
python sentinelgraph/remediation.py disable_payment_fault --apply
```

The investigation workflow requires explicit approval before it executes remediation. The remediation allowlist restricts which actions can be applied.

After changing the flag, Checkout may need to be restarted to pick up the updated flag evaluation.

### 6. Verify recovery

Generate payment traffic and run the verifier:

```bash
python sentinelgraph/verify_recovery.py
```

The verifier needs qualifying telemetry observations. It does not count an idle interval with no payment requests as a successful recovery check.

The flagd evaluation endpoint can change when Docker assigns a different host port. Set `FLAGD_EVALUATION_URL` to the current OFREP evaluation URL if necessary. For example:

```bash
export FLAGD_EVALUATION_URL="http://localhost:32804/ofrep/v1/evaluate/flags/paymentUnreachable"
python sentinelgraph/verify_recovery.py
```

The port above is from the tested environment and may change after a restart.

## Safety and Limitations

- Remediation is restricted to an explicit allowlist.
- Investigation uses deterministic graph queries rather than arbitrary model-generated Cypher.
- Applying remediation requires explicit approval.
- Dependency relationships provide context; they do not independently prove root cause.
- Recovery is based on observed telemetry and live flag evaluation, not just a configuration change.
- Persistent remediation memory can be retrieved by a fresh process.
- Memory creation and promotion are not yet a fully automated post-recovery workflow.
- This prototype does not implement a complete multi-agent coordination system or unrestricted autonomous operation.
- The demonstration depends on the OpenTelemetry Demo, Prometheus, and flagd configuration.
- The local `product-catalog` memory-limit adjustment is preserved in the OpenTelemetry Demo patch.

## Why FalkorDB Matters

FalkorDB is the incident knowledge graph and persistent remediation memory store. SentinelGraph queries the graph to connect incidents with affected services and dependencies, and retrieves reusable remediation experience across processes. Prometheus supplies independent operational evidence for recovery, while the graph retains incident state and verification results.

## Track Alignment

**Track 1 — Agents That Act on Connected Data:** graph-based incident and dependency investigation, telemetry evidence, allowlisted remediation, explicit approval, and independent recovery verification.

**Track 2 — Agent Memory & Coordination:** persistent remediation memory stored in FalkorDB and retrieved by a fresh investigation process, with a safety guard that avoids reapplying remediation to a recovered incident.

The implemented scope is a working prototype. Full multi-agent coordination and automated memory promotion remain future work.

## Repository

Source code: https://github.com/priyanshu-baran/SentinelGraph
