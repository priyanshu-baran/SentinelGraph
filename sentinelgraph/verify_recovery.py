import os
import time
import requests
import redis

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
FLAGD_EVALUATION_URL = os.getenv(
    "FLAGD_EVALUATION_URL",
    "http://localhost:32808/ofrep/v1/evaluate/flags/paymentUnreachable",
)
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
GRAPH_NAME = "sentinelgraph"

RPC_METHOD = "oteldemo.PaymentService/Charge"
REQUIRED_CLEAN_CHECKS = 3
MIN_CHECK_INTERVAL_SECONDS = 120


def query_prometheus():
    query = (
        'sum by (rpc_response_status_code) '
        '(increase(rpc_client_call_duration_seconds_count'
        '{service_name="checkout",rpc_method="'
        + RPC_METHOD
        + '"}[2m]))'
    )
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")

    counts = {"OK": 0.0, "UNAVAILABLE": 0.0}
    for series in payload["data"]["result"]:
        status = series.get("metric", {}).get(
            "rpc_response_status_code", "UNKNOWN"
        )
        if status in counts:
            counts[status] += float(series["value"][1])
    return counts


def get_flag_state():
    response = requests.post(
        FLAGD_EVALUATION_URL,
        json={},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("key") != "paymentUnreachable":
        raise RuntimeError(f"Unexpected flag response: {payload}")

    value = payload.get("value")
    variant = payload.get("variant")

    if not isinstance(value, bool):
        raise RuntimeError(f"Expected boolean flag value, got: {value!r}")

    print(f"Live flagd evaluation: variant={variant!r}, value={value}")
    return value


def main():
    r = redis.Redis(
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
    r.ping()

    flag_value = get_flag_state()
    counts = query_prometheus()
    now = int(time.time())

    print(f"paymentUnreachable flag: {flag_value}")
    print(f"Payment RPC counts in last 2m: {counts}")

    # A valid observation needs the fault disabled, successful traffic,
    # and no observed UNAVAILABLE responses.
    clean = (
        flag_value is False
        and counts["OK"] >= 1
        and counts["UNAVAILABLE"] == 0
    )

    if not clean:
        observed_failure = (
            flag_value is True or counts["UNAVAILABLE"] > 0
        )
        next_status = "active" if observed_failure else "recovery_check"

        query = """
        MATCH (i:Incident {id: 'incident-payment-unreachable-001'})
        SET i.recovery_clean_checks = 0,
            i.recovery_last_check_at = $now,
            i.status = $next_status
        RETURN i.id, i.status, i.recovery_clean_checks
        """
        result = r.execute_command(
            "GRAPH.QUERY",
            GRAPH_NAME,
            f"CYPHER now={now} next_status='{next_status}' " + query
        )
        print("Recovery not verified; clean streak reset.")
        print(result)
        return

    query = """
    MATCH (i:Incident {id: 'incident-payment-unreachable-001'})
    WITH i,
         coalesce(i.recovery_clean_checks, 0) AS old_checks,
         coalesce(i.recovery_last_check_at, 0) AS last_check
    WITH i, old_checks, last_check,
         CASE
           WHEN last_check = 0 THEN 1
           WHEN $now - last_check >= $interval THEN old_checks + 1
           ELSE old_checks
         END AS new_checks
    SET i.recovery_clean_checks = new_checks,
        i.status = CASE
            WHEN i.status = 'recovered' THEN 'recovered'
            ELSE 'recovery_check'
          END,
        i.recovery_last_check_at =
          CASE
            WHEN last_check = 0 OR $now - last_check >= $interval
            THEN $now
            ELSE last_check
          END,
        i.recent_failed_calls = 0,
        i.recent_successful_calls = $successful,
        i.source = 'prometheus'
    RETURN i.id, i.recovery_clean_checks, i.recovery_last_check_at
    """
    result = r.execute_command(
        "GRAPH.QUERY", GRAPH_NAME,
        f"CYPHER now={now} interval={MIN_CHECK_INTERVAL_SECONDS} "
        f"successful={counts['OK']} " + query,
    )
    print("Clean observation recorded:")
    print(result)

    query = """
    MATCH (i:Incident {id: 'incident-payment-unreachable-001'})
    WITH i
    WHERE coalesce(i.recovery_clean_checks, 0) >= $required
    SET i.status = 'recovered',
        i.recovery_verified_at = $now
    RETURN i.id, i.status, i.recovery_clean_checks,
           i.recovery_verified_at
    """
    result = r.execute_command(
        "GRAPH.QUERY", GRAPH_NAME,
        f"CYPHER required={REQUIRED_CLEAN_CHECKS} now={now} " + query,
    )
    print("Recovery result (empty means more clean checks are needed):")
    print(result)


if __name__ == "__main__":
    main()
