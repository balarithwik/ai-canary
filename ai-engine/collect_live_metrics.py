import json
import urllib.parse
import urllib.request

from discover_rollout import discover_rollout


PROMETHEUS_URL = "http://localhost:9090"

METRIC_WINDOW = "5m"
METRIC_WINDOW_SECONDS = 300

LATENCY_ACCEPTANCE_PERCENT = 15.0


# ============================================================
# PROMETHEUS
# ============================================================

def prometheus_query(query):

    params = urllib.parse.urlencode({
        "query": query
    })

    url = (
        f"{PROMETHEUS_URL}/api/v1/query?"
        f"{params}"
    )

    with urllib.request.urlopen(
        url,
        timeout=10
    ) as response:

        data = json.loads(
            response.read().decode("utf-8")
        )

    if data.get("status") != "success":

        raise RuntimeError(
            f"Prometheus query failed: {data}"
        )

    results = data[
        "data"
    ][
        "result"
    ]

    if not results:
        return 0.0

    return float(
        results[0]["value"][1]
    )


# ============================================================
# VERSION METRICS
# ============================================================

def collect_version_metrics(version):

    total_requests = prometheus_query(
        f'''
        sum(
          increase(
            http_requests_total{{
              exported_endpoint="/api/orders",
              version="{version}"
            }}[{METRIC_WINDOW}]
          )
        )
        '''
    )

    failed_requests = prometheus_query(
        f'''
        sum(
          increase(
            http_requests_total{{
              exported_endpoint="/api/orders",
              version="{version}",
              status="500"
            }}[{METRIC_WINDOW}]
          )
        )
        '''
    )

    latency_sum = prometheus_query(
        f'''
        sum(
          increase(
            http_request_duration_seconds_sum{{
              exported_endpoint="/api/orders",
              version="{version}"
            }}[{METRIC_WINDOW}]
          )
        )
        '''
    )

    latency_count = prometheus_query(
        f'''
        sum(
          increase(
            http_request_duration_seconds_count{{
              exported_endpoint="/api/orders",
              version="{version}"
            }}[{METRIC_WINDOW}]
          )
        )
        '''
    )

    if total_requests > 0:

        error_rate = (
            failed_requests
            /
            total_requests
        ) * 100

    else:

        error_rate = 0.0


    if latency_count > 0:

        avg_latency_seconds = (
            latency_sum
            /
            latency_count
        )

    else:

        avg_latency_seconds = 0.0


    throughput = (
        total_requests
        /
        METRIC_WINDOW_SECONDS
    )


    return {

        "version":
            version,

        "requests":
            total_requests,

        "failed_requests":
            failed_requests,

        "error_rate_percent":
            error_rate,

        "avg_latency_seconds":
            avg_latency_seconds,

        "avg_latency_ms":
            avg_latency_seconds * 1000,

        "throughput_rps":
            throughput
    }


# ============================================================
# FEATURE CALCULATION
# ============================================================

def calculate_features(
    stable,
    canary
):

    stable_latency = stable[
        "avg_latency_seconds"
    ]

    canary_latency = canary[
        "avg_latency_seconds"
    ]


    if stable_latency > 0:

        latency_change_percent = (

            (
                canary_latency
                -
                stable_latency
            )

            /
            stable_latency

        ) * 100

    else:

        latency_change_percent = 0.0


    error_delta = (

        canary[
            "error_rate_percent"
        ]

        -

        stable[
            "error_rate_percent"
        ]
    )


    if latency_change_percent <= LATENCY_ACCEPTANCE_PERCENT:

        latency_status = "WITHIN_ACCEPTABLE_RANGE"

    else:

        latency_status = "ABOVE_ACCEPTABLE_RANGE"


    return {

        "latency_change_percent":
            latency_change_percent,

        "error_rate_delta_pp":
            error_delta,

        "latency_acceptance_percent":
            LATENCY_ACCEPTANCE_PERCENT,

        "latency_reference_status":
            latency_status
    }


# ============================================================
# DISPLAY
# ============================================================

def print_version_metrics(
    title,
    metrics
):

    print(
        f"\n{title}"
    )

    print(
        "---------------------------"
    )

    print(
        f"Version        : "
        f"{metrics['version']}"
    )

    print(
        f"Requests       : "
        f"{metrics['requests']:.0f}"
    )

    print(
        f"Failed         : "
        f"{metrics['failed_requests']:.0f}"
    )

    print(
        f"Error Rate     : "
        f"{metrics['error_rate_percent']:.2f}%"
    )

    print(
        f"Average Latency: "
        f"{metrics['avg_latency_ms']:.2f} ms"
    )

    print(
        f"Throughput     : "
        f"{metrics['throughput_rps']:.2f} req/sec"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n=========================================="
    )

    print(
        " GENERIC LIVE TELEMETRY COLLECTOR"
    )

    print(
        "=========================================="
    )


    # --------------------------------------------------------
    # DISCOVER ROLLOUT
    # --------------------------------------------------------

    print(
        "\nDiscovering current rollout..."
    )

    rollout = discover_rollout()


    stable_version = rollout[
        "stable"
    ][
        "version"
    ]


    canary_active = rollout[
        "canary"
    ][
        "active"
    ]


    if not canary_active:

        print(
            "\nNo active Canary detected."
        )

        print(
            f"Current Stable: "
            f"{stable_version}"
        )

        print(
            "\nDeploy a Canary before running "
            "Stable vs Canary analysis."
        )

        return


    canary_version = rollout[
        "canary"
    ][
        "version"
    ]


    print(
        f"Stable discovered : "
        f"{stable_version}"
    )

    print(
        f"Stable weight     : "
        f"{rollout['stable']['weight']}%"
    )

    print(
        f"Canary discovered : "
        f"{canary_version}"
    )

    print(
        f"Canary weight     : "
        f"{rollout['canary']['weight']}%"
    )


    # --------------------------------------------------------
    # COLLECT PROMETHEUS METRICS
    # --------------------------------------------------------

    print(
        "\nCollecting live Prometheus metrics..."
    )


    stable_metrics = collect_version_metrics(
        stable_version
    )


    canary_metrics = collect_version_metrics(
        canary_version
    )


    print_version_metrics(
        "STABLE LIVE BASELINE",
        stable_metrics
    )


    print_version_metrics(
        "CANARY LIVE METRICS",
        canary_metrics
    )


    # --------------------------------------------------------
    # FEATURES
    # --------------------------------------------------------

    features = calculate_features(
        stable_metrics,
        canary_metrics
    )


    print(
        "\nSTABLE vs CANARY ANALYSIS"
    )

    print(
        "---------------------------"
    )


    print(
        f"Latency Change     : "
        f"{features['latency_change_percent']:+.2f}%"
    )


    print(
        f"Accepted Reference : "
        f"+{features['latency_acceptance_percent']:.2f}%"
    )


    print(
        f"Latency Status     : "
        f"{features['latency_reference_status']}"
    )


    print(
        f"Error Rate Delta   : "
        f"{features['error_rate_delta_pp']:+.2f} "
        f"percentage points"
    )


    # --------------------------------------------------------
    # JSON OUTPUT
    # --------------------------------------------------------

    output = {

        "rollout": rollout,

        "stable_metrics":
            stable_metrics,

        "canary_metrics":
            canary_metrics,

        "features":
            features
    }


    print(
        "\n=========================================="
    )

    print(
        " LIVE TELEMETRY COLLECTION COMPLETE"
    )

    print(
        "=========================================="
    )


    print(
        "\nJSON OUTPUT"
    )

    print(
        "---------------------------"
    )

    print(
        json.dumps(
            output,
            indent=2
        )
    )


if __name__ == "__main__":

    main()