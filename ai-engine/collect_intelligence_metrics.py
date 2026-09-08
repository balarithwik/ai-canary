import json
import subprocess
import urllib.parse
import urllib.request

from discover_rollout import discover_rollout


PROMETHEUS_URL = "http://localhost:9090"

METRIC_WINDOW = "5m"
RESOURCE_WINDOW = "2m"
METRIC_WINDOW_SECONDS = 300

LATENCY_ACCEPTANCE_PERCENT = 15.0


# ============================================================
# COMMAND HELPERS
# ============================================================

def run_json(command):

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )

    return json.loads(result.stdout)


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
# APPLICATION METRICS
# ============================================================

def collect_application_metrics(version):

    total_requests = prometheus_query(
        f'''
        sum(
          increase(
            http_requests_total{{
              endpoint="/api/orders",
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
              endpoint="/api/orders",
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
              endpoint="/api/orders",
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
              endpoint="/api/orders",
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
# POD DISCOVERY
# ============================================================

def discover_pods_for_replicaset(
    namespace,
    replica_set_name
):

    pods_data = run_json([
        "kubectl",
        "get",
        "pods",
        "-n",
        namespace,
        "-o",
        "json"
    ])

    matched_pods = []

    for pod in pods_data.get(
        "items",
        []
    ):

        owner_refs = (
            pod.get(
                "metadata",
                {}
            )
            .get(
                "ownerReferences",
                []
            )
        )

        belongs_to_rs = False

        for owner in owner_refs:

            if (
                owner.get("kind")
                == "ReplicaSet"

                and

                owner.get("name")
                == replica_set_name
            ):

                belongs_to_rs = True
                break


        if not belongs_to_rs:
            continue


        metadata = pod.get(
            "metadata",
            {}
        )

        status = pod.get(
            "status",
            {}
        )

        pod_name = metadata.get(
            "name",
            "UNKNOWN"
        )

        phase = status.get(
            "phase",
            "UNKNOWN"
        )


        container_statuses = status.get(
            "containerStatuses",
            []
        )


        ready = True

        restarts = 0


        if not container_statuses:

            ready = False


        for container_status in container_statuses:

            if not container_status.get(
                "ready",
                False
            ):

                ready = False


            restarts += int(
                container_status.get(
                    "restartCount",
                    0
                )
            )


        matched_pods.append({

            "name":
                pod_name,

            "phase":
                phase,

            "ready":
                ready,

            "restarts":
                restarts
        })


    return matched_pods


# ============================================================
# PROMETHEUS POD REGEX
# ============================================================

def build_pod_regex(pods):

    names = [
        pod["name"]
        for pod in pods
    ]

    if not names:
        return ""

    return "|".join(names)


# ============================================================
# RESOURCE METRICS
# ============================================================

def collect_resource_metrics(
    namespace,
    pods
):

    if not pods:

        return {

            "pod_count": 0,
            "ready_pods": 0,
            "healthy": False,
            "restarts": 0,
            "cpu_cores_total": 0.0,
            "cpu_millicores_per_pod": 0.0,
            "memory_mb_total": 0.0,
            "memory_mb_per_pod": 0.0
        }


    pod_regex = build_pod_regex(
        pods
    )


    cpu_cores = prometheus_query(
        f'''
        sum(
          rate(
            container_cpu_usage_seconds_total{{
              namespace="{namespace}",
              pod=~"{pod_regex}",
              container!="",
              container!="POD",
              image!=""
            }}[{RESOURCE_WINDOW}]
          )
        )
        '''
    )


    memory_bytes = prometheus_query(
        f'''
        sum(
          container_memory_working_set_bytes{{
            namespace="{namespace}",
            pod=~"{pod_regex}",
            container!="",
            container!="POD",
            image!=""
          }}
        )
        '''
    )


    pod_count = len(
        pods
    )


    ready_pods = sum(

        1

        for pod in pods

        if (
            pod["phase"] == "Running"
            and pod["ready"]
        )
    )


    restart_count = sum(
        pod["restarts"]
        for pod in pods
    )


    healthy = (
        pod_count > 0
        and ready_pods == pod_count
        and restart_count == 0
    )


    memory_mb = (
        memory_bytes
        /
        1024
        /
        1024
    )


    return {

        "pod_count":
            pod_count,

        "ready_pods":
            ready_pods,

        "healthy":
            healthy,

        "restarts":
            restart_count,

        "cpu_cores_total":
            cpu_cores,

        "cpu_millicores_per_pod":
            (
                cpu_cores
                *
                1000
                /
                pod_count
            ),

        "memory_mb_total":
            memory_mb,

        "memory_mb_per_pod":
            (
                memory_mb
                /
                pod_count
            )
    }


# ============================================================
# FEATURE CALCULATION
# ============================================================

def percent_change(
    stable_value,
    canary_value
):

    if stable_value <= 0:

        return 0.0

    return (

        (
            canary_value
            -
            stable_value
        )

        /

        stable_value

    ) * 100


def calculate_features(
    stable_app,
    canary_app,
    stable_resource,
    canary_resource
):

    latency_change = percent_change(

        stable_app[
            "avg_latency_ms"
        ],

        canary_app[
            "avg_latency_ms"
        ]
    )


    cpu_change = percent_change(

        stable_resource[
            "cpu_millicores_per_pod"
        ],

        canary_resource[
            "cpu_millicores_per_pod"
        ]
    )


    memory_change = percent_change(

        stable_resource[
            "memory_mb_per_pod"
        ],

        canary_resource[
            "memory_mb_per_pod"
        ]
    )


    error_delta = (

        canary_app[
            "error_rate_percent"
        ]

        -

        stable_app[
            "error_rate_percent"
        ]
    )


    if (
        latency_change
        <= LATENCY_ACCEPTANCE_PERCENT
    ):

        latency_status = (
            "WITHIN_ACCEPTABLE_RANGE"
        )

    else:

        latency_status = (
            "ABOVE_ACCEPTABLE_RANGE"
        )


    return {

        "latency_change_percent":
            latency_change,

        "latency_acceptance_percent":
            LATENCY_ACCEPTANCE_PERCENT,

        "latency_reference_status":
            latency_status,

        "error_rate_delta_pp":
            error_delta,

        "cpu_change_percent":
            cpu_change,

        "memory_change_percent":
            memory_change,

        "stable_pod_health":
            stable_resource[
                "healthy"
            ],

        "canary_pod_health":
            canary_resource[
                "healthy"
            ],

        "stable_restarts":
            stable_resource[
                "restarts"
            ],

        "canary_restarts":
            canary_resource[
                "restarts"
            ]
    }


# ============================================================
# DISPLAY HELPERS
# ============================================================

def print_application_metrics(
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


def print_resource_metrics(
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
        f"Pods           : "
        f"{metrics['pod_count']}"
    )

    print(
        f"Ready Pods     : "
        f"{metrics['ready_pods']}"
    )

    print(
        f"Healthy        : "
        f"{metrics['healthy']}"
    )

    print(
        f"Restarts       : "
        f"{metrics['restarts']}"
    )

    print(
        f"CPU / Pod      : "
        f"{metrics['cpu_millicores_per_pod']:.2f} m"
    )

    print(
        f"Memory / Pod   : "
        f"{metrics['memory_mb_per_pod']:.2f} MB"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n=========================================="
    )

    print(
        " AI DEPLOYMENT TELEMETRY ENGINE"
    )

    print(
        "=========================================="
    )


    print(
        "\nDiscovering Rollout..."
    )


    rollout = discover_rollout()


    if not rollout[
        "canary"
    ][
        "active"
    ]:

        print(
            "\nNo active Canary detected."
        )

        return


    namespace = rollout[
        "namespace"
    ]


    stable_version = rollout[
        "stable"
    ][
        "version"
    ]


    canary_version = rollout[
        "canary"
    ][
        "version"
    ]


    stable_rs = rollout[
        "stable"
    ][
        "replica_set"
    ]


    canary_rs = rollout[
        "canary"
    ][
        "replica_set"
    ]


    print(
        f"Stable : "
        f"{stable_version} "
        f"({rollout['stable']['weight']}%)"
    )

    print(
        f"Canary : "
        f"{canary_version} "
        f"({rollout['canary']['weight']}%)"
    )


    # ========================================================
    # APPLICATION METRICS
    # ========================================================

    print(
        "\nCollecting application metrics..."
    )


    stable_app = (
        collect_application_metrics(
            stable_version
        )
    )


    canary_app = (
        collect_application_metrics(
            canary_version
        )
    )


    # ========================================================
    # POD DISCOVERY
    # ========================================================

    stable_pods = (
        discover_pods_for_replicaset(
            namespace,
            stable_rs
        )
    )


    canary_pods = (
        discover_pods_for_replicaset(
            namespace,
            canary_rs
        )
    )


    # ========================================================
    # RESOURCE METRICS
    # ========================================================

    print(
        "Collecting Kubernetes resource metrics..."
    )


    stable_resource = (
        collect_resource_metrics(
            namespace,
            stable_pods
        )
    )


    canary_resource = (
        collect_resource_metrics(
            namespace,
            canary_pods
        )
    )


    # ========================================================
    # FEATURE ENGINE
    # ========================================================

    features = calculate_features(

        stable_app,
        canary_app,
        stable_resource,
        canary_resource
    )


    # ========================================================
    # DISPLAY
    # ========================================================

    print_application_metrics(
        "STABLE APPLICATION",
        stable_app
    )


    print_resource_metrics(
        "STABLE INFRASTRUCTURE",
        stable_resource
    )


    print_application_metrics(
        "CANARY APPLICATION",
        canary_app
    )


    print_resource_metrics(
        "CANARY INFRASTRUCTURE",
        canary_resource
    )


    print(
        "\nINTELLIGENCE FEATURES"
    )

    print(
        "---------------------------"
    )


    print(
        f"Latency Change  : "
        f"{features['latency_change_percent']:+.2f}%"
    )


    print(
        f"Reference       : "
        f"+{features['latency_acceptance_percent']:.2f}%"
    )


    print(
        f"Latency Status  : "
        f"{features['latency_reference_status']}"
    )


    print(
        f"Error Delta     : "
        f"{features['error_rate_delta_pp']:+.2f} pp"
    )


    print(
        f"CPU Change      : "
        f"{features['cpu_change_percent']:+.2f}%"
    )


    print(
        f"Memory Change   : "
        f"{features['memory_change_percent']:+.2f}%"
    )


    print(
        f"Stable Healthy  : "
        f"{features['stable_pod_health']}"
    )


    print(
        f"Canary Healthy  : "
        f"{features['canary_pod_health']}"
    )


    print(
        f"Canary Restarts : "
        f"{features['canary_restarts']}"
    )


    # ========================================================
    # JSON
    # ========================================================

    output = {

        "rollout":
            rollout,

        "stable": {

            "application":
                stable_app,

            "infrastructure":
                stable_resource
        },

        "canary": {

            "application":
                canary_app,

            "infrastructure":
                canary_resource
        },

        "features":
            features
    }


    print(
        "\n=========================================="
    )

    print(
        " TELEMETRY ENGINE COMPLETE"
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
