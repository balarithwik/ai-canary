import json
import time

import collect_intelligence_metrics as telemetry
from discover_rollout import discover_rollout


# ============================================================
# CONFIGURATION
# ============================================================

OBSERVATION_COUNT = 4
OBSERVATION_INTERVAL_SECONDS = 30

# Trend analysis should look at recent behavior,
# rather than the older 5-minute aggregate.
telemetry.METRIC_WINDOW = "1m"
telemetry.METRIC_WINDOW_SECONDS = 60
telemetry.RESOURCE_WINDOW = "1m"


# ============================================================
# HELPERS
# ============================================================

def percent_change(stable_value, canary_value):

    if stable_value <= 0:
        return 0.0

    return (
        (canary_value - stable_value)
        / stable_value
    ) * 100


def collect_observation():

    rollout = discover_rollout()

    if not rollout["canary"]["active"]:
        raise RuntimeError(
            "No active Canary deployment detected."
        )

    namespace = rollout["namespace"]

    stable_version = rollout["stable"]["version"]
    canary_version = rollout["canary"]["version"]

    stable_rs = rollout["stable"]["replica_set"]
    canary_rs = rollout["canary"]["replica_set"]

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    stable_app = telemetry.collect_application_metrics(
        stable_version
    )

    canary_app = telemetry.collect_application_metrics(
        canary_version
    )

    # --------------------------------------------------------
    # PODS
    # --------------------------------------------------------

    stable_pods = telemetry.discover_pods_for_replicaset(
        namespace,
        stable_rs
    )

    canary_pods = telemetry.discover_pods_for_replicaset(
        namespace,
        canary_rs
    )

    # --------------------------------------------------------
    # INFRASTRUCTURE
    # --------------------------------------------------------

    stable_resource = telemetry.collect_resource_metrics(
        namespace,
        stable_pods
    )

    canary_resource = telemetry.collect_resource_metrics(
        namespace,
        canary_pods
    )

    # --------------------------------------------------------
    # FEATURES
    # --------------------------------------------------------

    latency_change = percent_change(
        stable_app["avg_latency_ms"],
        canary_app["avg_latency_ms"]
    )

    cpu_change = percent_change(
        stable_resource["cpu_millicores_per_pod"],
        canary_resource["cpu_millicores_per_pod"]
    )

    memory_change = percent_change(
        stable_resource["memory_mb_per_pod"],
        canary_resource["memory_mb_per_pod"]
    )

    error_delta = (
        canary_app["error_rate_percent"]
        -
        stable_app["error_rate_percent"]
    )

    return {

        "stable_version":
            stable_version,

        "canary_version":
            canary_version,

        "stable_latency_ms":
            stable_app["avg_latency_ms"],

        "canary_latency_ms":
            canary_app["avg_latency_ms"],

        "latency_change_percent":
            latency_change,

        "stable_error_rate":
            stable_app["error_rate_percent"],

        "canary_error_rate":
            canary_app["error_rate_percent"],

        "error_delta_pp":
            error_delta,

        "stable_cpu_m":
            stable_resource[
                "cpu_millicores_per_pod"
            ],

        "canary_cpu_m":
            canary_resource[
                "cpu_millicores_per_pod"
            ],

        "cpu_change_percent":
            cpu_change,

        "stable_memory_mb":
            stable_resource[
                "memory_mb_per_pod"
            ],

        "canary_memory_mb":
            canary_resource[
                "memory_mb_per_pod"
            ],

        "memory_change_percent":
            memory_change,

        "canary_healthy":
            canary_resource["healthy"],

        "canary_restarts":
            canary_resource["restarts"]
    }


# ============================================================
# TREND CLASSIFICATION
# ============================================================

def classify_numeric_trend(
    values,
    meaningful_change
):

    if len(values) < 2:
        return "UNKNOWN"

    first = values[0]
    last = values[-1]

    change = last - first

    increases = 0
    decreases = 0

    for index in range(
        1,
        len(values)
    ):

        previous = values[
            index - 1
        ]

        current = values[
            index
        ]

        if current > previous:
            increases += 1

        elif current < previous:
            decreases += 1

    required_direction_count = max(
        2,
        len(values) - 2
    )

    if (
        change >= meaningful_change
        and
        increases >= required_direction_count
    ):
        return "DEGRADING"

    if (
        change <= -meaningful_change
        and
        decreases >= required_direction_count
    ):
        return "IMPROVING"

    return "STABLE"


def classify_latency_trend(
    values,
    meaningful_change_percent=10.0,
    minimum_change_ms=5.0
):

    if len(values) < 2:
        return "UNKNOWN"

    first = values[0]

    if first <= 0:
        return "UNKNOWN"

    meaningful_change_ms = max(
        minimum_change_ms,
        abs(first)
        * meaningful_change_percent
        / 100.0
    )

    return classify_numeric_trend(
        values,
        meaningful_change=meaningful_change_ms
    )


def build_trend_summary(observations):

    # IMPORTANT:
    # Severity still compares Canary against Stable.
    #
    # Trend direction, however, must follow the Canary's own
    # absolute latency over time. Using Stable-vs-Canary delta
    # here can create a false DEGRADING trend when Stable itself
    # fluctuates between observation windows.

    canary_latency_values = [
        item["canary_latency_ms"]
        for item in observations
    ]

    latency_delta_values = [
        item["latency_change_percent"]
        for item in observations
    ]

    cpu_values = [
        item["canary_cpu_m"]
        for item in observations
    ]

    memory_values = [
        item["canary_memory_mb"]
        for item in observations
    ]

    error_values = [
        item["canary_error_rate"]
        for item in observations
    ]

    latency_trend = classify_latency_trend(
        canary_latency_values,
        meaningful_change_percent=10.0,
        minimum_change_ms=5.0
    )

    cpu_trend = classify_numeric_trend(
        cpu_values,
        meaningful_change=1.0
    )

    memory_trend = classify_numeric_trend(
        memory_values,
        meaningful_change=5.0
    )

    error_trend = classify_numeric_trend(
        error_values,
        meaningful_change=1.0
    )

    # Overall trend prioritizes the most important
    # application degradation indicators.

    if (
        latency_trend == "DEGRADING"
        or error_trend == "DEGRADING"
    ):

        overall = "DEGRADING"

    elif (
        latency_trend == "IMPROVING"
        and error_trend != "DEGRADING"
    ):

        overall = "IMPROVING"

    else:

        overall = "STABLE"

    return {

        "overall_trend":
            overall,

        "latency_trend":
            latency_trend,

        "error_trend":
            error_trend,

        "cpu_trend":
            cpu_trend,

        "memory_trend":
            memory_trend,

        # Absolute Canary latency drives trend classification.
        "canary_latency_sequence_ms":
            canary_latency_values,

        # Stable-vs-Canary delta is still preserved for
        # severity/comparison visibility.
        "latency_sequence_percent":
            latency_delta_values,

        "error_sequence_percent":
            error_values,

        "cpu_sequence_millicores":
            cpu_values,

        "memory_sequence_mb":
            memory_values
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n=========================================="
    )

    print(
        " AI MULTI-WINDOW TREND ENGINE"
    )

    print(
        "=========================================="
    )

    print(
        f"\nObservations : {OBSERVATION_COUNT}"
    )

    print(
        f"Interval     : "
        f"{OBSERVATION_INTERVAL_SECONDS} seconds"
    )

    print(
        "Metric Window: 1 minute"
    )


    observations = []


    for number in range(
        1,
        OBSERVATION_COUNT + 1
    ):

        print(
            "\n------------------------------------------"
        )

        print(
            f" OBSERVATION {number}/{OBSERVATION_COUNT}"
        )

        print(
            "------------------------------------------"
        )

        try:

            observation = collect_observation()

        except Exception as error:

            print(
                f"\nObservation failed: {error}"
            )

            return


        observations.append(
            observation
        )


        print(
            f"Stable Latency : "
            f"{observation['stable_latency_ms']:.2f} ms"
        )

        print(
            f"Canary Latency : "
            f"{observation['canary_latency_ms']:.2f} ms"
        )

        print(
            f"Latency Change : "
            f"{observation['latency_change_percent']:+.2f}%"
        )

        print(
            f"Error Delta    : "
            f"{observation['error_delta_pp']:+.2f} pp"
        )

        print(
            f"Stable CPU     : "
            f"{observation['stable_cpu_m']:.2f} m"
        )

        print(
            f"Canary CPU     : "
            f"{observation['canary_cpu_m']:.2f} m"
        )

        print(
            f"Canary Memory  : "
            f"{observation['canary_memory_mb']:.2f} MB"
        )

        print(
            f"Canary Healthy : "
            f"{observation['canary_healthy']}"
        )

        print(
            f"Canary Restarts: "
            f"{observation['canary_restarts']}"
        )


        if number < OBSERVATION_COUNT:

            print(
                f"\nWaiting "
                f"{OBSERVATION_INTERVAL_SECONDS} seconds "
                f"for next observation..."
            )

            time.sleep(
                OBSERVATION_INTERVAL_SECONDS
            )


    # ========================================================
    # ANALYZE TREND
    # ========================================================

    trends = build_trend_summary(
        observations
    )


    print(
        "\n=========================================="
    )

    print(
        " TREND ANALYSIS"
    )

    print(
        "=========================================="
    )


    print(
        f"\nOverall Trend : "
        f"{trends['overall_trend']}"
    )

    print(
        f"Latency Trend : "
        f"{trends['latency_trend']}"
    )

    print(
        f"Error Trend   : "
        f"{trends['error_trend']}"
    )

    print(
        f"CPU Trend     : "
        f"{trends['cpu_trend']}"
    )

    print(
        f"Memory Trend  : "
        f"{trends['memory_trend']}"
    )


    print(
        "\nCanary Latency Sequence"
    )

    print(
        "---------------------------"
    )

    for index, value in enumerate(
        trends[
            "canary_latency_sequence_ms"
        ],
        start=1
    ):

        print(
            f"Window {index}: "
            f"{value:.2f} ms"
        )


    print(
        "\nStable-vs-Canary Latency Delta"
    )

    print(
        "---------------------------"
    )

    for index, value in enumerate(
        trends[
            "latency_sequence_percent"
        ],
        start=1
    ):

        print(
            f"Window {index}: "
            f"{value:+.2f}%"
        )


    output = {

        "observations":
            observations,

        "trends":
            trends
    }


    print(
        "\n=========================================="
    )

    print(
        " TREND ENGINE COMPLETE"
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
