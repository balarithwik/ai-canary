import json
import re
import subprocess
import time
import statistics

import collect_intelligence_metrics as telemetry
import trend_analysis
from discover_rollout import discover_rollout


# ============================================================
# CONFIGURATION
# ============================================================

OBSERVATION_COUNT = 4
OBSERVATION_INTERVAL_SECONDS = 30

# Recent live observation window
telemetry.METRIC_WINDOW = "1m"
telemetry.METRIC_WINDOW_SECONDS = 60
telemetry.RESOURCE_WINDOW = "1m"

# Normal production reference
LATENCY_ACCEPTANCE_PERCENT = 15.0

# Minimum traffic required
MIN_STABLE_REQUESTS = 20
MIN_CANARY_REQUESTS = 20

# Minimum number of usable windows required for a trusted
# median-based deployment assessment.
MIN_VALID_OBSERVATIONS = 2

# Catastrophic hard guardrails only
HARD_ERROR_RATE_PERCENT = 10.0
HARD_LATENCY_RATIO = 3.0
HARD_CPU_SATURATION_PERCENT = 95.0
HARD_MEMORY_SATURATION_PERCENT = 95.0
HARD_RESTART_COUNT = 3

OUTPUT_FILE = "ai_risk_context.json"


# ============================================================
# COMMAND HELPER
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
# RESOURCE QUANTITY PARSING
# ============================================================

def parse_cpu_millicores(value):

    if value is None:
        return 0.0

    value = str(value).strip()

    if value.endswith("m"):
        return float(value[:-1])

    try:
        return float(value) * 1000
    except ValueError:
        return 0.0


def parse_memory_mb(value):

    if value is None:
        return 0.0

    value = str(value).strip()

    match = re.match(
        r"^([0-9.]+)([A-Za-z]+)?$",
        value
    )

    if not match:
        return 0.0

    number = float(
        match.group(1)
    )

    unit = (
        match.group(2)
        or ""
    )

    multipliers = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1000 / (1024 * 1024),
        "M": 1000 * 1000 / (1024 * 1024),
        "G": 1000 * 1000 * 1000 / (1024 * 1024)
    }

    if unit in multipliers:

        return (
            number
            *
            multipliers[unit]
        )

    return 0.0


# ============================================================
# GET RESOURCE LIMITS FROM REPLICASET
# ============================================================

def get_resource_limits(
    namespace,
    replica_set_name
):

    rs = run_json([
        "kubectl",
        "get",
        "rs",
        replica_set_name,
        "-n",
        namespace,
        "-o",
        "json"
    ])

    containers = (
        rs.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )

    if not containers:

        return {
            "cpu_limit_m": 0.0,
            "memory_limit_mb": 0.0
        }

    resources = (
        containers[0]
        .get("resources", {})
    )

    limits = resources.get(
        "limits",
        {}
    )

    return {

        "cpu_limit_m":
            parse_cpu_millicores(
                limits.get("cpu")
            ),

        "memory_limit_mb":
            parse_memory_mb(
                limits.get("memory")
            )
    }


# ============================================================
# PERCENT CHANGE
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


# ============================================================
# SATURATION
# ============================================================

def saturation_percent(
    usage,
    limit
):

    if limit <= 0:
        return 0.0

    return (
        usage
        /
        limit
    ) * 100


# ============================================================
# SINGLE OBSERVATION
# ============================================================

def collect_observation():

    rollout = discover_rollout()

    if not rollout[
        "canary"
    ][
        "active"
    ]:

        raise RuntimeError(
            "No active Canary detected."
        )

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


    # --------------------------------------------------------
    # APPLICATION METRICS
    # --------------------------------------------------------

    stable_app = (
        telemetry.collect_application_metrics(
            stable_version
        )
    )

    canary_app = (
        telemetry.collect_application_metrics(
            canary_version
        )
    )


    # --------------------------------------------------------
    # POD DISCOVERY
    # --------------------------------------------------------

    stable_pods = (
        telemetry.discover_pods_for_replicaset(
            namespace,
            stable_rs
        )
    )

    canary_pods = (
        telemetry.discover_pods_for_replicaset(
            namespace,
            canary_rs
        )
    )


    # --------------------------------------------------------
    # RESOURCE USAGE
    # --------------------------------------------------------

    stable_resource = (
        telemetry.collect_resource_metrics(
            namespace,
            stable_pods
        )
    )

    canary_resource = (
        telemetry.collect_resource_metrics(
            namespace,
            canary_pods
        )
    )


    # --------------------------------------------------------
    # RESOURCE LIMITS
    # --------------------------------------------------------

    stable_limits = (
        get_resource_limits(
            namespace,
            stable_rs
        )
    )

    canary_limits = (
        get_resource_limits(
            namespace,
            canary_rs
        )
    )


    # --------------------------------------------------------
    # LATENCY
    # --------------------------------------------------------

    latency_change = percent_change(

        stable_app[
            "avg_latency_ms"
        ],

        canary_app[
            "avg_latency_ms"
        ]
    )


    if (
        stable_app[
            "avg_latency_ms"
        ]
        > 0
    ):

        latency_ratio = (

            canary_app[
                "avg_latency_ms"
            ]

            /

            stable_app[
                "avg_latency_ms"
            ]
        )

    else:

        latency_ratio = 0.0


    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    error_delta = (

        canary_app[
            "error_rate_percent"
        ]

        -

        stable_app[
            "error_rate_percent"
        ]
    )


    # --------------------------------------------------------
    # CPU
    # --------------------------------------------------------

    stable_cpu = (
        stable_resource[
            "cpu_millicores_per_pod"
        ]
    )

    canary_cpu = (
        canary_resource[
            "cpu_millicores_per_pod"
        ]
    )


    cpu_change = percent_change(
        stable_cpu,
        canary_cpu
    )


    stable_cpu_saturation = (
        saturation_percent(

            stable_cpu,

            stable_limits[
                "cpu_limit_m"
            ]
        )
    )


    canary_cpu_saturation = (
        saturation_percent(

            canary_cpu,

            canary_limits[
                "cpu_limit_m"
            ]
        )
    )


    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    stable_memory = (
        stable_resource[
            "memory_mb_per_pod"
        ]
    )

    canary_memory = (
        canary_resource[
            "memory_mb_per_pod"
        ]
    )


    memory_change = percent_change(
        stable_memory,
        canary_memory
    )


    stable_memory_saturation = (
        saturation_percent(

            stable_memory,

            stable_limits[
                "memory_limit_mb"
            ]
        )
    )


    canary_memory_saturation = (
        saturation_percent(

            canary_memory,

            canary_limits[
                "memory_limit_mb"
            ]
        )
    )


    return {

        # Needed by existing trend engine

        "stable_version":
            stable_version,

        "canary_version":
            canary_version,

        "stable_latency_ms":
            stable_app[
                "avg_latency_ms"
            ],

        "canary_latency_ms":
            canary_app[
                "avg_latency_ms"
            ],

        "latency_change_percent":
            latency_change,

        "stable_error_rate":
            stable_app[
                "error_rate_percent"
            ],

        "canary_error_rate":
            canary_app[
                "error_rate_percent"
            ],

        "error_delta_pp":
            error_delta,

        "stable_cpu_m":
            stable_cpu,

        "canary_cpu_m":
            canary_cpu,

        "cpu_change_percent":
            cpu_change,

        "stable_memory_mb":
            stable_memory,

        "canary_memory_mb":
            canary_memory,

        "memory_change_percent":
            memory_change,

        "canary_healthy":
            canary_resource[
                "healthy"
            ],

        "canary_restarts":
            canary_resource[
                "restarts"
            ],


        # Additional intelligence context

        "stable_requests":
            stable_app[
                "requests"
            ],

        "canary_requests":
            canary_app[
                "requests"
            ],

        "latency_ratio":
            latency_ratio,

        "stable_cpu_limit_m":
            stable_limits[
                "cpu_limit_m"
            ],

        "canary_cpu_limit_m":
            canary_limits[
                "cpu_limit_m"
            ],

        "stable_cpu_saturation_percent":
            stable_cpu_saturation,

        "canary_cpu_saturation_percent":
            canary_cpu_saturation,

        "stable_memory_limit_mb":
            stable_limits[
                "memory_limit_mb"
            ],

        "canary_memory_limit_mb":
            canary_limits[
                "memory_limit_mb"
            ],

        "stable_memory_saturation_percent":
            stable_memory_saturation,

        "canary_memory_saturation_percent":
            canary_memory_saturation,

        "stable_pod_count":
            stable_resource[
                "pod_count"
            ],

        "stable_ready_pods":
            stable_resource[
                "ready_pods"
            ],

        "canary_pod_count":
            canary_resource[
                "pod_count"
            ],

        "canary_ready_pods":
            canary_resource[
                "ready_pods"
            ],

        "stable_restarts":
            stable_resource[
                "restarts"
            ]
    }



# ============================================================
# ROBUST MULTI-WINDOW AGGREGATION
# ============================================================

def _safe_number(value, default=0.0):

    try:

        if value is None:
            return float(default)

        return float(value)

    except (
        TypeError,
        ValueError
    ):

        return float(default)


def _median_from(
    observations,
    key,
    default=0.0
):

    values = []

    for observation in observations:

        value = _safe_number(
            observation.get(
                key
            ),
            default=None
        )

        if value is None:
            continue

        values.append(
            value
        )

    if not values:
        return float(default)

    return float(
        statistics.median(
            values
        )
    )


def application_window_validation(
    observation
):

    reasons = []

    stable_latency = _safe_number(
        observation.get(
            "stable_latency_ms"
        )
    )

    canary_latency = _safe_number(
        observation.get(
            "canary_latency_ms"
        )
    )

    stable_requests = _safe_number(
        observation.get(
            "stable_requests"
        )
    )

    canary_requests = _safe_number(
        observation.get(
            "canary_requests"
        )
    )

    if stable_latency <= 0:

        reasons.append(
            "Stable latency sample is unavailable"
        )

    if canary_latency <= 0:

        reasons.append(
            "Canary latency sample is unavailable"
        )

    if (
        stable_requests
        < MIN_STABLE_REQUESTS
    ):

        reasons.append(
            "Insufficient Stable requests"
        )

    if (
        canary_requests
        < MIN_CANARY_REQUESTS
    ):

        reasons.append(
            "Insufficient Canary requests"
        )

    return {
        "valid":
            len(
                reasons
            ) == 0,

        "reasons":
            reasons
    }


def aggregate_observations(
    observations
):

    if not observations:

        raise RuntimeError(
            "No observations available for aggregation."
        )


    valid_observations = []
    excluded_windows = []


    for index, observation in enumerate(
        observations,
        start=1
    ):

        validation = (
            application_window_validation(
                observation
            )
        )

        if validation[
            "valid"
        ]:

            valid_observations.append(
                observation
            )

        else:

            excluded_windows.append({
                "window":
                    index,

                "reasons":
                    validation[
                        "reasons"
                    ]
            })


    # Use only application-valid windows for representative
    # Stable-vs-Canary comparison. If none are valid, preserve
    # the raw observations so the safety layer can PAUSE rather
    # than fabricating a healthy result.
    aggregation_source = (
        valid_observations
        if valid_observations
        else observations
    )


    latest = dict(
        observations[
            -1
        ]
    )

    representative = dict(
        latest
    )


    # --------------------------------------------------------
    # APPLICATION LATENCY
    # --------------------------------------------------------

    representative[
        "stable_latency_ms"
    ] = _median_from(
        aggregation_source,
        "stable_latency_ms"
    )

    representative[
        "canary_latency_ms"
    ] = _median_from(
        aggregation_source,
        "canary_latency_ms"
    )

    representative[
        "latency_change_percent"
    ] = percent_change(

        representative[
            "stable_latency_ms"
        ],

        representative[
            "canary_latency_ms"
        ]
    )


    if (
        representative[
            "stable_latency_ms"
        ]
        > 0
    ):

        representative[
            "latency_ratio"
        ] = (

            representative[
                "canary_latency_ms"
            ]

            /

            representative[
                "stable_latency_ms"
            ]
        )

    else:

        representative[
            "latency_ratio"
        ] = 0.0


    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    representative[
        "stable_error_rate"
    ] = _median_from(
        aggregation_source,
        "stable_error_rate"
    )

    representative[
        "canary_error_rate"
    ] = _median_from(
        aggregation_source,
        "canary_error_rate"
    )

    representative[
        "error_delta_pp"
    ] = (

        representative[
            "canary_error_rate"
        ]

        -

        representative[
            "stable_error_rate"
        ]
    )


    # --------------------------------------------------------
    # CPU
    # --------------------------------------------------------

    representative[
        "stable_cpu_m"
    ] = _median_from(
        aggregation_source,
        "stable_cpu_m"
    )

    representative[
        "canary_cpu_m"
    ] = _median_from(
        aggregation_source,
        "canary_cpu_m"
    )

    representative[
        "cpu_change_percent"
    ] = percent_change(

        representative[
            "stable_cpu_m"
        ],

        representative[
            "canary_cpu_m"
        ]
    )

    representative[
        "stable_cpu_saturation_percent"
    ] = _median_from(
        aggregation_source,
        "stable_cpu_saturation_percent"
    )

    representative[
        "canary_cpu_saturation_percent"
    ] = _median_from(
        aggregation_source,
        "canary_cpu_saturation_percent"
    )


    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    representative[
        "stable_memory_mb"
    ] = _median_from(
        aggregation_source,
        "stable_memory_mb"
    )

    representative[
        "canary_memory_mb"
    ] = _median_from(
        aggregation_source,
        "canary_memory_mb"
    )

    representative[
        "memory_change_percent"
    ] = percent_change(

        representative[
            "stable_memory_mb"
        ],

        representative[
            "canary_memory_mb"
        ]
    )

    representative[
        "stable_memory_saturation_percent"
    ] = _median_from(
        aggregation_source,
        "stable_memory_saturation_percent"
    )

    representative[
        "canary_memory_saturation_percent"
    ] = _median_from(
        aggregation_source,
        "canary_memory_saturation_percent"
    )


    # --------------------------------------------------------
    # TRAFFIC
    # --------------------------------------------------------

    representative[
        "stable_requests"
    ] = _median_from(
        aggregation_source,
        "stable_requests"
    )

    representative[
        "canary_requests"
    ] = _median_from(
        aggregation_source,
        "canary_requests"
    )


    # --------------------------------------------------------
    # POD HEALTH
    #
    # Pod readiness is intentionally current-state driven.
    # Restart count is conservative: preserve the highest value
    # observed across the analysis cycle.
    # --------------------------------------------------------

    representative[
        "canary_pod_count"
    ] = latest[
        "canary_pod_count"
    ]

    representative[
        "canary_ready_pods"
    ] = latest[
        "canary_ready_pods"
    ]

    representative[
        "canary_healthy"
    ] = latest[
        "canary_healthy"
    ]

    representative[
        "canary_restarts"
    ] = max(
        int(
            _safe_number(
                item.get(
                    "canary_restarts"
                )
            )
        )
        for item in observations
    )


    data_quality = {

        "aggregation_method":
            "MEDIAN",

        "total_windows":
            len(
                observations
            ),

        "valid_windows":
            len(
                valid_observations
            ),

        "excluded_windows":
            len(
                excluded_windows
            ),

        "minimum_valid_windows":
            MIN_VALID_OBSERVATIONS,

        "enough_valid_windows":
            (
                len(
                    valid_observations
                )
                >=
                MIN_VALID_OBSERVATIONS
            ),

        "valid_window_numbers":
            [
                index
                for index, observation
                in enumerate(
                    observations,
                    start=1
                )
                if application_window_validation(
                    observation
                )[
                    "valid"
                ]
            ],

        "excluded_window_details":
            excluded_windows
    }


    return (
        representative,
        valid_observations,
        data_quality
    )


# ============================================================
# SIGNAL CLASSIFICATION
# ============================================================

def classify_latency(
    latency_change
):

    if latency_change <= LATENCY_ACCEPTANCE_PERCENT:

        return "NORMAL"

    if latency_change <= 30:

        return "ELEVATED"

    if latency_change <= 60:

        return "HIGH"

    return "SEVERE"


def classify_error(
    error_delta
):

    if error_delta <= 0.5:

        return "NORMAL"

    if error_delta <= 2:

        return "ELEVATED"

    if error_delta <= 5:

        return "HIGH"

    return "SEVERE"


def classify_saturation(
    saturation
):

    if saturation < 50:

        return "LOW"

    if saturation < 70:

        return "MODERATE"

    if saturation < 85:

        return "HIGH"

    return "CRITICAL"


# ============================================================
# HARD SAFETY GUARDRAILS
# ============================================================

def evaluate_guardrails(
    observation
):

    reasons = []


    # --------------------------------------------------------
    # INSUFFICIENT TRAFFIC
    # --------------------------------------------------------

    if (
        observation[
            "stable_requests"
        ]
        < MIN_STABLE_REQUESTS
    ):

        reasons.append(
            "Insufficient Stable traffic"
        )

        return {
            "triggered": True,
            "mandatory_action": "PAUSE",
            "reasons": reasons
        }


    if (
        observation[
            "canary_requests"
        ]
        < MIN_CANARY_REQUESTS
    ):

        reasons.append(
            "Insufficient Canary traffic"
        )

        return {
            "triggered": True,
            "mandatory_action": "PAUSE",
            "reasons": reasons
        }


    # --------------------------------------------------------
    # CRITICAL ERRORS
    # --------------------------------------------------------

    if (
        observation[
            "canary_error_rate"
        ]
        >= HARD_ERROR_RATE_PERCENT
    ):

        reasons.append(

            f"Canary error rate "
            f"{observation['canary_error_rate']:.2f}% "
            f"exceeds critical "
            f"{HARD_ERROR_RATE_PERCENT:.2f}%"
        )


    # --------------------------------------------------------
    # CRITICAL LATENCY
    # --------------------------------------------------------

    if (
        observation[
            "latency_ratio"
        ]
        >= HARD_LATENCY_RATIO
    ):

        reasons.append(

            f"Canary latency is "
            f"{observation['latency_ratio']:.2f}x "
            f"Stable"
        )


    # --------------------------------------------------------
    # CPU SATURATION
    # --------------------------------------------------------

    if (
        observation[
            "canary_cpu_saturation_percent"
        ]
        >= HARD_CPU_SATURATION_PERCENT
    ):

        reasons.append(

            f"Canary CPU saturation "
            f"{observation['canary_cpu_saturation_percent']:.2f}%"
        )


    # --------------------------------------------------------
    # MEMORY SATURATION
    # --------------------------------------------------------

    if (
        observation[
            "canary_memory_saturation_percent"
        ]
        >= HARD_MEMORY_SATURATION_PERCENT
    ):

        reasons.append(

            f"Canary memory saturation "
            f"{observation['canary_memory_saturation_percent']:.2f}%"
        )


    # --------------------------------------------------------
    # POD FAILURE
    # --------------------------------------------------------

    if (
        observation[
            "canary_ready_pods"
        ]
        == 0
    ):

        reasons.append(
            "No Canary pods are Ready"
        )


    if (
        observation[
            "canary_restarts"
        ]
        >= HARD_RESTART_COUNT
    ):

        reasons.append(

            f"Canary restart count "
            f"{observation['canary_restarts']} "
            f"exceeds safe limit"
        )


    if reasons:

        return {
            "triggered": True,
            "mandatory_action": "ROLLBACK",
            "reasons": reasons
        }


    # --------------------------------------------------------
    # PARTIAL POD HEALTH
    # --------------------------------------------------------

    if not observation[
        "canary_healthy"
    ]:

        return {

            "triggered": True,

            "mandatory_action":
                "PAUSE",

            "reasons": [
                "Canary pod health is degraded"
            ]
        }


    return {

        "triggered":
            False,

        "mandatory_action":
            "NONE",

        "reasons":
            []
    }


# ============================================================
# BUILD AI CONTEXT
# ============================================================


# ============================================================
# AI OBSERVATION HISTORY
# ============================================================

def build_observation_history(
    observations
):

    history = []

    for index, observation in enumerate(
        observations,
        start=1
    ):

        validation = (
            application_window_validation(
                observation
            )
        )

        history.append({

            "window":
                index,

            "valid":
                validation[
                    "valid"
                ],

            "validation_reasons":
                validation[
                    "reasons"
                ],

            "stable_latency_ms":
                round(
                    _safe_number(
                        observation.get(
                            "stable_latency_ms"
                        )
                    ),
                    2
                ),

            "canary_latency_ms":
                round(
                    _safe_number(
                        observation.get(
                            "canary_latency_ms"
                        )
                    ),
                    2
                ),

            "latency_change_percent":
                round(
                    _safe_number(
                        observation.get(
                            "latency_change_percent"
                        )
                    ),
                    2
                ),

            "stable_error_rate_percent":
                round(
                    _safe_number(
                        observation.get(
                            "stable_error_rate"
                        )
                    ),
                    3
                ),

            "canary_error_rate_percent":
                round(
                    _safe_number(
                        observation.get(
                            "canary_error_rate"
                        )
                    ),
                    3
                ),

            "error_delta_percentage_points":
                round(
                    _safe_number(
                        observation.get(
                            "error_delta_pp"
                        )
                    ),
                    3
                ),

            "canary_cpu_millicores":
                round(
                    _safe_number(
                        observation.get(
                            "canary_cpu_m"
                        )
                    ),
                    2
                ),

            "canary_cpu_saturation_percent":
                round(
                    _safe_number(
                        observation.get(
                            "canary_cpu_saturation_percent"
                        )
                    ),
                    2
                ),

            "canary_memory_mb":
                round(
                    _safe_number(
                        observation.get(
                            "canary_memory_mb"
                        )
                    ),
                    2
                ),

            "canary_memory_saturation_percent":
                round(
                    _safe_number(
                        observation.get(
                            "canary_memory_saturation_percent"
                        )
                    ),
                    2
                ),

            "canary_pods":
                safe_int(
                    observation.get(
                        "canary_pod_count"
                    ),
                    0
                )
                if "safe_int" in globals()
                else int(
                    _safe_number(
                        observation.get(
                            "canary_pod_count"
                        )
                    )
                ),

            "ready_pods":
                safe_int(
                    observation.get(
                        "canary_ready_pods"
                    ),
                    0
                )
                if "safe_int" in globals()
                else int(
                    _safe_number(
                        observation.get(
                            "canary_ready_pods"
                        )
                    )
                ),

            "healthy":
                bool(
                    observation.get(
                        "canary_healthy",
                        False
                    )
                ),

            "restarts":
                int(
                    _safe_number(
                        observation.get(
                            "canary_restarts"
                        )
                    )
                ),

            "stable_requests":
                round(
                    _safe_number(
                        observation.get(
                            "stable_requests"
                        )
                    ),
                    2
                ),

            "canary_requests":
                round(
                    _safe_number(
                        observation.get(
                            "canary_requests"
                        )
                    ),
                    2
                )
        })

    return history


def build_ai_context(
    rollout,
    representative,
    observations,
    trends,
    data_quality
):

    latest = observations[
        -1
    ]


    signals = {

        "latency": {

            "stable_ms":
                representative[
                    "stable_latency_ms"
                ],

            "canary_ms":
                representative[
                    "canary_latency_ms"
                ],

            "change_percent":
                representative[
                    "latency_change_percent"
                ],

            "acceptable_reference_percent":
                LATENCY_ACCEPTANCE_PERCENT,

            "severity":
                classify_latency(
                    representative[
                        "latency_change_percent"
                    ]
                ),

            "trend":
                trends[
                    "latency_trend"
                ]
        },


        "errors": {

            "stable_percent":
                representative[
                    "stable_error_rate"
                ],

            "canary_percent":
                representative[
                    "canary_error_rate"
                ],

            "delta_percentage_points":
                representative[
                    "error_delta_pp"
                ],

            "severity":
                classify_error(
                    representative[
                        "error_delta_pp"
                    ]
                ),

            "trend":
                trends[
                    "error_trend"
                ]
        },


        "cpu": {

            "stable_millicores":
                representative[
                    "stable_cpu_m"
                ],

            "canary_millicores":
                representative[
                    "canary_cpu_m"
                ],

            "change_percent":
                representative[
                    "cpu_change_percent"
                ],

            "canary_limit_millicores":
                representative[
                    "canary_cpu_limit_m"
                ],

            "canary_saturation_percent":
                representative[
                    "canary_cpu_saturation_percent"
                ],

            "severity":
                classify_saturation(
                    representative[
                        "canary_cpu_saturation_percent"
                    ]
                ),

            "trend":
                trends[
                    "cpu_trend"
                ]
        },


        "memory": {

            "stable_mb":
                representative[
                    "stable_memory_mb"
                ],

            "canary_mb":
                representative[
                    "canary_memory_mb"
                ],

            "change_percent":
                representative[
                    "memory_change_percent"
                ],

            "canary_limit_mb":
                representative[
                    "canary_memory_limit_mb"
                ],

            "canary_saturation_percent":
                representative[
                    "canary_memory_saturation_percent"
                ],

            "severity":
                classify_saturation(
                    representative[
                        "canary_memory_saturation_percent"
                    ]
                ),

            "trend":
                trends[
                    "memory_trend"
                ]
        },


        "pod_health": {

            "canary_pods":
                representative[
                    "canary_pod_count"
                ],

            "ready_pods":
                representative[
                    "canary_ready_pods"
                ],

            "healthy":
                representative[
                    "canary_healthy"
                ],

            "restarts":
                representative[
                    "canary_restarts"
                ]
        }
    }


    # Hard safety conditions remain based on the latest live
    # observation so median aggregation can never hide a
    # catastrophic current-state failure.
    guardrail = evaluate_guardrails(
        latest
    )


    # If there are too few trustworthy application windows,
    # fail safely with PAUSE rather than allowing promotion from
    # incomplete telemetry.
    if (
        not data_quality[
            "enough_valid_windows"
        ]
        and
        (
            not guardrail[
                "triggered"
            ]
            or
            guardrail[
                "mandatory_action"
            ] != "ROLLBACK"
        )
    ):

        guardrail = {

            "triggered":
                True,

            "mandatory_action":
                "PAUSE",

            "reasons": [
                (
                    "Insufficient valid telemetry windows: "
                    f"{data_quality['valid_windows']}/"
                    f"{data_quality['total_windows']} valid; "
                    f"minimum required is "
                    f"{data_quality['minimum_valid_windows']}"
                )
            ]
        }


    context = {

        "deployment": {

            "rollout":
                rollout[
                    "rollout"
                ],

            "phase":
                rollout[
                    "phase"
                ],

            "stable_version":
                rollout[
                    "stable"
                ][
                    "version"
                ],

            "canary_version":
                rollout[
                    "canary"
                ][
                    "version"
                ],

            "stable_weight":
                rollout[
                    "stable"
                ][
                    "weight"
                ],

            "canary_weight":
                rollout[
                    "canary"
                ][
                    "weight"
                ]
        },


        "traffic": {

            "stable_requests":
                representative[
                    "stable_requests"
                ],

            "canary_requests":
                representative[
                    "canary_requests"
                ]
        },


        "signals":
            signals,


        "overall_trend":
            trends[
                "overall_trend"
            ],


        "hard_guardrail":
            guardrail,


        "data_quality":
            data_quality,


        "aggregation": {

            "method":
                data_quality[
                    "aggregation_method"
                ],

            "representative_values":
                "Median of valid telemetry windows"
        },


        "trend_summary": {

            "latency":
                trends.get(
                    "latency_trend",
                    "UNKNOWN"
                ),

            "errors":
                trends.get(
                    "error_trend",
                    "UNKNOWN"
                ),

            "cpu":
                trends.get(
                    "cpu_trend",
                    "UNKNOWN"
                ),

            "memory":
                trends.get(
                    "memory_trend",
                    "UNKNOWN"
                ),

            "overall":
                trends.get(
                    "overall_trend",
                    "UNKNOWN"
                )
        },


        "observation_history":
            build_observation_history(
                observations
            ),


        "observation_count":
            len(
                observations
            ),


        "valid_observation_count":
            data_quality[
                "valid_windows"
            ]
    }


    return context


# ============================================================
# DISPLAY
# ============================================================

def print_context(
    context
):

    deployment = context[
        "deployment"
    ]

    signals = context[
        "signals"
    ]

    guardrail = context[
        "hard_guardrail"
    ]


    print(
        "\n=========================================="
    )

    print(
        " AI DEPLOYMENT RISK CONTEXT"
    )

    print(
        "=========================================="
    )


    print(
        "\nDEPLOYMENT"
    )

    print(
        "---------------------------"
    )

    print(
        f"Stable        : "
        f"{deployment['stable_version']} "
        f"({deployment['stable_weight']}%)"
    )

    print(
        f"Canary        : "
        f"{deployment['canary_version']} "
        f"({deployment['canary_weight']}%)"
    )


    data_quality = context.get(
        "data_quality",
        {}
    )


    print(
        "\nTELEMETRY AGGREGATION"
    )

    print(
        "---------------------------"
    )

    print(
        f"Method        : "
        f"{data_quality.get('aggregation_method', 'UNKNOWN')}"
    )

    print(
        f"Valid Windows : "
        f"{data_quality.get('valid_windows', 0)}/"
        f"{data_quality.get('total_windows', 0)}"
    )

    print(
        f"Excluded      : "
        f"{data_quality.get('excluded_windows', 0)}"
    )

    print(
        f"History Saved : "
        f"{len(context.get('observation_history', []))} windows"
    )

    if data_quality.get(
        "excluded_window_details"
    ):

        for item in data_quality[
            "excluded_window_details"
        ]:

            print(
                f"Window {item['window']}     : "
                f"EXCLUDED - "
                f"{'; '.join(item['reasons'])}"
            )


    print(
        "\nLATENCY"
    )

    print(
        "---------------------------"
    )

    print(
        f"Stable        : "
        f"{signals['latency']['stable_ms']:.2f} ms"
    )

    print(
        f"Canary        : "
        f"{signals['latency']['canary_ms']:.2f} ms"
    )

    print(
        f"Change        : "
        f"{signals['latency']['change_percent']:+.2f}%"
    )

    print(
        f"Reference     : "
        f"+{LATENCY_ACCEPTANCE_PERCENT:.2f}%"
    )

    print(
        f"Severity      : "
        f"{signals['latency']['severity']}"
    )

    print(
        f"Trend         : "
        f"{signals['latency']['trend']}"
    )


    print(
        "\nERROR RATE"
    )

    print(
        "---------------------------"
    )

    print(
        f"Stable        : "
        f"{signals['errors']['stable_percent']:.2f}%"
    )

    print(
        f"Canary        : "
        f"{signals['errors']['canary_percent']:.2f}%"
    )

    print(
        f"Delta         : "
        f"{signals['errors']['delta_percentage_points']:+.2f} pp"
    )

    print(
        f"Severity      : "
        f"{signals['errors']['severity']}"
    )

    print(
        f"Trend         : "
        f"{signals['errors']['trend']}"
    )


    print(
        "\nCPU"
    )

    print(
        "---------------------------"
    )

    print(
        f"Canary Usage  : "
        f"{signals['cpu']['canary_millicores']:.2f} m"
    )

    print(
        f"Canary Limit  : "
        f"{signals['cpu']['canary_limit_millicores']:.2f} m"
    )

    print(
        f"Saturation    : "
        f"{signals['cpu']['canary_saturation_percent']:.2f}%"
    )

    print(
        f"Severity      : "
        f"{signals['cpu']['severity']}"
    )

    print(
        f"Trend         : "
        f"{signals['cpu']['trend']}"
    )


    print(
        "\nMEMORY"
    )

    print(
        "---------------------------"
    )

    print(
        f"Canary Usage  : "
        f"{signals['memory']['canary_mb']:.2f} MB"
    )

    print(
        f"Canary Limit  : "
        f"{signals['memory']['canary_limit_mb']:.2f} MB"
    )

    print(
        f"Saturation    : "
        f"{signals['memory']['canary_saturation_percent']:.2f}%"
    )

    print(
        f"Severity      : "
        f"{signals['memory']['severity']}"
    )

    print(
        f"Trend         : "
        f"{signals['memory']['trend']}"
    )


    print(
        "\nPOD HEALTH"
    )

    print(
        "---------------------------"
    )

    print(
        f"Pods          : "
        f"{signals['pod_health']['canary_pods']}"
    )

    print(
        f"Ready         : "
        f"{signals['pod_health']['ready_pods']}"
    )

    print(
        f"Healthy       : "
        f"{signals['pod_health']['healthy']}"
    )

    print(
        f"Restarts      : "
        f"{signals['pod_health']['restarts']}"
    )


    print(
        "\nOVERALL BEHAVIOUR"
    )

    print(
        "---------------------------"
    )

    print(
        f"Trend         : "
        f"{context['overall_trend']}"
    )


    print(
        "\nHARD SAFETY GUARDRAIL"
    )

    print(
        "---------------------------"
    )

    print(
        f"Triggered     : "
        f"{guardrail['triggered']}"
    )

    print(
        f"Mandatory     : "
        f"{guardrail['mandatory_action']}"
    )


    if guardrail[
        "reasons"
    ]:

        for reason in guardrail[
            "reasons"
        ]:

            print(
                f"Reason        : {reason}"
            )

    else:

        print(
            "Reason        : "
            "No catastrophic condition detected"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n=========================================="
    )

    print(
        " BUILDING AI RISK CONTEXT"
    )

    print(
        "=========================================="
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


    observations = []


    for number in range(
        1,
        OBSERVATION_COUNT + 1
    ):

        print(
            f"\nCollecting observation "
            f"{number}/{OBSERVATION_COUNT}..."
        )


        try:

            observation = (
                collect_observation()
            )

        except Exception as error:

            print(
                f"\nCollection failed: {error}"
            )

            return


        observations.append(
            observation
        )


        print(
            f"Latency Change : "
            f"{observation['latency_change_percent']:+.2f}%"
        )

        print(
            f"Canary Error   : "
            f"{observation['canary_error_rate']:.2f}%"
        )

        print(
            f"Canary CPU     : "
            f"{observation['canary_cpu_m']:.2f} m"
        )

        print(
            f"CPU Saturation : "
            f"{observation['canary_cpu_saturation_percent']:.2f}%"
        )

        print(
            f"Memory Sat.    : "
            f"{observation['canary_memory_saturation_percent']:.2f}%"
        )


        if number < OBSERVATION_COUNT:

            print(
                f"Waiting "
                f"{OBSERVATION_INTERVAL_SECONDS} seconds..."
            )

            time.sleep(
                OBSERVATION_INTERVAL_SECONDS
            )


    (
        representative,
        valid_observations,
        data_quality
    ) = aggregate_observations(
        observations
    )


    print(
        "\nTelemetry aggregation:"
    )

    print(
        f"Method        : "
        f"{data_quality['aggregation_method']}"
    )

    print(
        f"Valid Windows : "
        f"{data_quality['valid_windows']}/"
        f"{data_quality['total_windows']}"
    )

    print(
        f"Excluded      : "
        f"{data_quality['excluded_windows']}"
    )


    # Trend analysis uses only telemetry-valid windows whenever
    # enough are available. This prevents a zero/no-traffic
    # latency sample from creating UNKNOWN or false degradation.
    if (
        len(
            valid_observations
        )
        >= 2
    ):

        trend_observations = (
            valid_observations
        )

    else:

        trend_observations = (
            observations
        )


    trends = (
        trend_analysis.build_trend_summary(
            trend_observations
        )
    )


    context = build_ai_context(
        rollout,
        representative,
        observations,
        trends,
        data_quality
    )


    print_context(
        context
    )


    # --------------------------------------------------------
    # SAVE CONTEXT
    # --------------------------------------------------------

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            context,
            file,
            indent=2
        )


    print(
        "\n=========================================="
    )

    print(
        " AI CONTEXT READY"
    )

    print(
        "=========================================="
    )

    print(
        f"\nSaved to: {OUTPUT_FILE}"
    )

    print(
        "\nThis JSON will be the structured "
        "input for the AI Risk Engine."
    )


if __name__ == "__main__":

    main()