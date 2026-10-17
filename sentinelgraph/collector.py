import os
import sys
import requests
import redis

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
GRAPH_NAME = "sentinelgraph"

RPC_METHOD = "oteldemo.PaymentService/Charge"


def query_prometheus(promql):
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": promql},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")

    return payload["data"]["result"]


def get_rpc_counts():
    recent_query = (
        'sum by (rpc_response_status_code) '
        '(increase(rpc_client_call_duration_seconds_count'
        '{service_name="checkout",rpc_method="'
        + RPC_METHOD + '"}[2m]))'
    )

    total_query = (
        'sum by (rpc_response_status_code) '
        '(rpc_client_call_duration_seconds_count'
        '{service_name="checkout",rpc_method="'
        + RPC_METHOD + '"})'
    )

    def collect(query):
        counts = {"OK": 0.0, "UNAVAILABLE": 0.0}
        for series in query_prometheus(query):
            status = series.get("metric", {}).get(
                "rpc_response_status_code", "UNKNOWN"
            )
            if status in counts:
                counts[status] += float(series["value"][1])
        return counts

    return {
        "recent": collect(recent_query),
        "total": collect(total_query),
    }


def upsert_graph(r, counts):
    recent = counts["recent"]
    total_counts = counts["total"]

    recent_failed = recent["UNAVAILABLE"]
    recent_succeeded = recent["OK"]
    failed_total = total_counts["UNAVAILABLE"]
    succeeded_total = total_counts["OK"]

    recent_total = recent_failed + recent_succeeded
    failure_share = (
        recent_failed / recent_total if recent_total else 0.0
    )

    # Failures keep the incident active.
    # A clean window alone is not enough to prove recovery.
    MIN_SUCCESSFUL_CALLS = 10

    if recent_failed > 0:
        incident_status = "active"
    elif recent_succeeded >= MIN_SUCCESSFUL_CALLS:
        incident_status = "recovery_candidate"
    else:
        incident_status = "recovery_check"

    query = """
    MERGE (c:Service {name: 'checkout'})
    SET c.criticality = 'critical',
        c.namespace = 'opentelemetry-demo'
    MERGE (p:Service {name: 'payment'})
    SET p.namespace = 'opentelemetry-demo'
    MERGE (c)-[d:DEPENDS_ON {operation: $rpc_method}]->(p)
    SET d.protocol = 'grpc'
    MERGE (i:Incident {id: 'incident-payment-unreachable-001'})
    SET i.title = 'Payment service unreachable',
        i.status = CASE
            WHEN $recent_failed > 0 THEN 'active'
            WHEN i.status = 'recovered' THEN 'recovered'
            ELSE $incident_status
        END,
        i.error_type = 'UNAVAILABLE',
        i.rpc_method = $rpc_method,
        i.recent_failed_calls = $recent_failed,
        i.recent_successful_calls = $recent_succeeded,
        i.failed_calls_total = $failed_total,
        i.successful_calls_total = $succeeded_total,
        i.failure_share_observed = $failure_share,
        i.source = 'prometheus'
    MERGE (i)-[:AFFECTS]->(c)
    MERGE (i)-[:INVOLVES]->(p)
    RETURN i.id, i.status,
           i.recent_failed_calls, i.recent_successful_calls,
           i.failed_calls_total, i.successful_calls_total,
           i.failure_share_observed
    """

    params = (
        f"CYPHER rpc_method='{RPC_METHOD}' "
        f"incident_status='{incident_status}' "
        f"recent_failed={recent_failed} "
        f"recent_succeeded={recent_succeeded} "
        f"failed_total={failed_total} "
        f"succeeded_total={succeeded_total} "
        f"failure_share={failure_share} "
    )

    return r.execute_command(
        "GRAPH.QUERY", GRAPH_NAME, params + query
    )


def main():
    r = redis.Redis(
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
    r.ping()

    counts = get_rpc_counts()
    print("Recent payment RPC counts (2m):", counts["recent"])
    print("Lifetime payment RPC counts:", counts["total"])

    if sum(counts["recent"].values()) == 0 and sum(counts["total"].values()) == 0:
        print(
            "No matching RPC counters found. Graph was not updated.",
            file=sys.stderr,
        )
        sys.exit(2)

    result = upsert_graph(r, counts)
    print("FalkorDB graph updated:")
    print(result)


if __name__ == "__main__":
    main()
