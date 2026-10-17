import subprocess
import sys
from pathlib import Path

import redis

from remediation import ALLOWED_REMEDIATIONS

GRAPH_NAME = "sentinelgraph"
INCIDENT_ID = "incident-payment-unreachable-001"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REMEDIATION_SCRIPT = Path(__file__).resolve().parent / "remediation.py"


def graph_query(r, query):
    result = r.execute_command("GRAPH.QUERY", GRAPH_NAME, query)
    if not isinstance(result, list) or len(result) < 2:
        raise RuntimeError(f"Unexpected FalkorDB result: {result}")
    headers, rows = result[0], result[1]
    return [dict(zip(headers, row)) for row in rows]


def get_incident(r):
    rows = graph_query(
        r,
        """
        MATCH (i:Incident {id: 'incident-payment-unreachable-001'})
        RETURN i.id AS id,
               i.status AS status,
               i.error_type AS error_type,
               i.recent_failed_calls AS recent_failed,
               i.recent_successful_calls AS recent_successful,
               i.failure_share_observed AS failure_share
        """,
    )
    return rows[0] if rows else None

def get_dependency_path(r):
    return graph_query(
	r,
	"""
	Match (i:Incident {id: 'incident-payment-unreachable-001'})
	      -[:AFFECTS]->(affected:Service)
	      -[d:DEPENDS_ON]->(dependency:Service)
	RETURN affected.name AS affected_service,
	       d.protocol AS protocol,
	       d.operation AS operation,
	       dependency.name AS dependency_service,
	       affected.criticality AS criticality
	""",
    )


def get_matching_memories(r, incident_type):
    safe_type = incident_type.replace("'", "\\'")
    return graph_query(
        r,
        f"""
        MATCH (m:RemediationMemory)
        WHERE m.incident_type = '{safe_type}'
          AND m.reusable = true
          AND m.outcome = 'recovered'
        RETURN m.id AS id,
               m.symptom AS symptom,
               m.action AS action,
               m.flag AS flag,
               m.target_variant AS target_variant,
               m.outcome AS outcome,
               m.verified_clean_checks AS clean_checks,
               m.verification_method AS verification_method
        ORDER BY m.verified_clean_checks DESC
        """,
    )


def main():
    r = redis.Redis(
        host="localhost",
        port=6379,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
    r.ping()

    incident = get_incident(r)
    if not incident:
        print("No matching incident found in FalkorDB.")
        return

    print("\n=== SENTINELGRAPH INVESTIGATION ===")
    print(f"Incident: {incident['id']}")
    print(f"Status: {incident['status']}")
    print(f"Error type: {incident.get('error_type')}")
    print(f"Recent failed calls: {incident.get('recent_failed')}")
    print(f"Recent successful calls: {incident.get('recent_successful')}")
    print(f"Observed failure share: {incident.get('failure_share')}")

    dependencies = get_dependency_path(r)
    print("\n=== GRAPH DEPENDENCY EVIDENCE ===")

    if dependencies:
        for dependency in dependencies:
            print(
                f"{dependency['affected_service']} "
                f"--[{dependency.get('protocol')} / "
                f"{dependency.get('operation')}]--> "
                f"{dependency['dependency_service']}"
            )
            print(
                f"  Affected service criticality: "
                f"{dependency.get('criticality')}"
            )
    else:
        print("No dependency path found for this incident.")


    incident_type = "payment_service_unreachable"
    memories = get_matching_memories(r, incident_type)

    print(f"\nVerified matching memories found: {len(memories)}")
    for index, memory in enumerate(memories, start=1):
        print(f"\nMemory {index}: {memory.get('id')}")
        print(f"  Symptom: {memory.get('symptom')}")
        print(f"  Previously successful action: {memory.get('action')}")
        print(f"  Target: {memory.get('flag')} -> {memory.get('target_variant')}")
        print(f"  Outcome: {memory.get('outcome')}")
        print(f"  Clean checks: {memory.get('clean_checks')}")
        print(f"  Verification method: {memory.get('verification_method')}")

    if incident["status"] != "active":
        print(
            "\nNo action proposed: the incident is not active. "
            "Memory retrieval succeeded, but a recovered incident "
            "must not be remediated again."
        )
        return

    failed = float(incident.get("recent_failed") or 0)
    if failed <= 0:
        print(
            "\nNo action proposed: the graph does not currently show "
            "recent failed calls. Refresh telemetry before investigating."
        )
        return

    if not memories:
        print("\nNo verified matching memory. Refusing to guess a remediation.")
        return

    action = memories[0].get("action")
    if action not in ALLOWED_REMEDIATIONS:
        print(f"\nRefusing action not present in the allowlist: {action}")
        return

    spec = ALLOWED_REMEDIATIONS[action]
    if (
        memories[0].get("flag") != spec["flag"]
        or memories[0].get("target_variant") != spec["target_variant"]
    ):
        print("\nMemory does not match the current allowlist. Refusing action.")
        return

    print("\n=== RECOMMENDATION ===")
    print(f"Evidence: {failed} recent UNAVAILABLE calls in the graph.")
    print(f"Prior verified action: {action}")
    print(f"Allowed change: {spec['flag']} -> {spec['target_variant']}")
    print("This is a recommendation, not authorization to execute.")

    approval = input(
        "\nType APPLY to execute this allowlisted action, "
        "or anything else to cancel: "
    ).strip()

    if approval != "APPLY":
        print("Cancelled. No remediation executed.")
        return

    subprocess.run(
        [sys.executable, str(REMEDIATION_SCRIPT), action, "--apply"],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=90,
    )


if __name__ == "__main__":
    main()
