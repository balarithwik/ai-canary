import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

OLLAMA_URL = "http://localhost:11434"
FAST_AI_MODEL = "qwen3:4b-instruct"
DEEP_AI_MODEL = "qwen3:4b-instruct"
OLLAMA_MODEL = FAST_AI_MODEL

BASE_DIR = Path(__file__).resolve().parent

CONTEXT_FILE = BASE_DIR / "ai_risk_context.json"
OUTPUT_FILE = BASE_DIR / "ai_decision.json"

HEALTHY_PROMOTION_MAX_BASE_RISK = 10
OLLAMA_TIMEOUT_SECONDS = 300
OLLAMA_RETRY_TIMEOUT_SECONDS = 240
FAST_AI_TIMEOUT_SECONDS = 120
DEEP_AI_TIMEOUT_SECONDS = 360
AI_CONTEXT_SIZE = 4096
MODEL_KEEP_ALIVE = "10m"
MODEL_WARMUP_TIMEOUT_SECONDS = 180
DEEP_FALLBACK_MODEL = DEEP_AI_MODEL


# ============================================================
# DECISION PRIORITY
# ============================================================

DECISION_PRIORITY = {
    "PROMOTE": 1,
    "PAUSE": 2,
    "ROLLBACK": 3
}


# ============================================================
# BASIC HELPERS
# ============================================================

def load_json(path):

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def save_json(path, data):

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2
        )


def safe_float(value, default=0.0):

    try:

        if value is None:
            return float(default)

        return float(value)

    except (TypeError, ValueError):

        return float(default)


def safe_int(value, default=0):

    try:

        if value is None:
            return int(default)

        return int(value)

    except (TypeError, ValueError):

        return int(default)


def safe_upper(value, default="UNKNOWN"):

    if value is None:
        return default

    value = str(value).strip()

    if not value:
        return default

    return value.upper()


def clamp(value, minimum, maximum):

    return max(
        minimum,
        min(
            maximum,
            value
        )
    )


# ============================================================
# RISK LEVEL
# ============================================================

def get_risk_level(score):

    score = safe_float(score)

    if score <= 30:
        return "LOW"

    if score <= 60:
        return "MEDIUM"

    if score <= 80:
        return "HIGH"

    return "CRITICAL"


# ============================================================
# SCORE ACTION
# ============================================================

def get_score_action(score):

    score = safe_float(score)

    if score <= 30:
        return "PROMOTE"

    if score <= 80:
        return "PAUSE"

    return "ROLLBACK"


# ============================================================
# DETERMINISTIC BASE RISK
# ============================================================

def calculate_base_risk(context):

    signals = context.get(
        "signals",
        {}
    )

    latency = signals.get(
        "latency",
        {}
    )

    errors = signals.get(
        "errors",
        {}
    )

    cpu = signals.get(
        "cpu",
        {}
    )

    memory = signals.get(
        "memory",
        {}
    )

    pod_health = signals.get(
        "pod_health",
        {}
    )

    score = 0
    factors = []


    # ========================================================
    # LATENCY
    # ========================================================

    latency_change = safe_float(
        latency.get(
            "change_percent",
            0
        )
    )

    if latency_change <= 15:

        latency_risk = 0

        factors.append(
            "Latency is within the normal 15% reference range."
        )

    elif latency_change <= 30:

        latency_risk = 15

        factors.append(
            "Latency regression is elevated compared with Stable."
        )

    elif latency_change <= 60:

        latency_risk = 30

        factors.append(
            "Latency regression is high compared with Stable."
        )

    else:

        latency_risk = 45

        factors.append(
            "Severe latency regression detected compared with Stable."
        )

    score += latency_risk


    # ========================================================
    # ERROR RATE
    # ========================================================

    error_delta = safe_float(
        errors.get(
            "delta_percentage_points",
            0
        )
    )

    if error_delta <= 0.5:

        error_risk = 0

        factors.append(
            "Error rate remains normal relative to Stable."
        )

    elif error_delta <= 2:

        error_risk = 10

        factors.append(
            "Canary error rate is slightly elevated."
        )

    elif error_delta <= 5:

        error_risk = 25

        factors.append(
            "Canary error rate shows a significant increase."
        )

    else:

        error_risk = 40

        factors.append(
            "Severe error-rate regression detected."
        )

    score += error_risk


    # ========================================================
    # CPU
    # ========================================================

    cpu_saturation = safe_float(
        cpu.get(
            "canary_saturation_percent",
            0
        )
    )

    if cpu_saturation < 50:

        cpu_risk = 0

        factors.append(
            "CPU remains far below resource saturation."
        )

    elif cpu_saturation < 70:

        cpu_risk = 5

        factors.append(
            "CPU utilization is moderately elevated."
        )

    elif cpu_saturation < 85:

        cpu_risk = 12

        factors.append(
            "CPU utilization is high."
        )

    else:

        cpu_risk = 20

        factors.append(
            "CPU is approaching critical saturation."
        )

    score += cpu_risk


    # ========================================================
    # MEMORY
    # ========================================================

    memory_saturation = safe_float(
        memory.get(
            "canary_saturation_percent",
            0
        )
    )

    if memory_saturation < 50:

        memory_risk = 0

        factors.append(
            "Memory remains far below resource saturation."
        )

    elif memory_saturation < 70:

        memory_risk = 5

        factors.append(
            "Memory utilization is moderately elevated."
        )

    elif memory_saturation < 85:

        memory_risk = 12

        factors.append(
            "Memory utilization is high."
        )

    else:

        memory_risk = 20

        factors.append(
            "Memory is approaching critical saturation."
        )

    score += memory_risk


    # ========================================================
    # POD HEALTH
    # ========================================================

    healthy = bool(
        pod_health.get(
            "healthy",
            False
        )
    )

    if not healthy:

        score += 25

        factors.append(
            "Canary pod health is degraded."
        )

    else:

        factors.append(
            "Canary pods are healthy."
        )


    # ========================================================
    # RESTARTS
    # ========================================================

    restarts = safe_int(
        pod_health.get(
            "restarts",
            0
        )
    )

    if restarts > 0:

        restart_risk = min(
            restarts * 8,
            24
        )

        score += restart_risk

        factors.append(
            f"Canary containers restarted {restarts} time(s)."
        )

    else:

        factors.append(
            "No Canary container restarts detected."
        )


    # ========================================================
    # OVERALL TREND
    # ========================================================

    overall_trend = safe_upper(
        context.get(
            "overall_trend",
            "UNKNOWN"
        )
    )

    if overall_trend == "DEGRADING":

        score += 12

        factors.append(
            "Overall deployment trend is degrading."
        )

    elif overall_trend == "IMPROVING":

        score -= 5

        factors.append(
            "Overall deployment trend is improving."
        )

    else:

        factors.append(
            "Overall deployment trend is stable."
        )


    # ========================================================
    # LATENCY TREND
    # ========================================================

    latency_trend = safe_upper(
        latency.get(
            "trend",
            "UNKNOWN"
        )
    )

    if latency_trend == "DEGRADING":

        score += 8

        factors.append(
            "Latency trend is degrading across observation windows."
        )


    # ========================================================
    # ERROR TREND
    # ========================================================

    error_trend = safe_upper(
        errors.get(
            "trend",
            "UNKNOWN"
        )
    )

    if error_trend == "DEGRADING":

        score += 8

        factors.append(
            "Error-rate trend is degrading."
        )


    # ========================================================
    # CPU TREND
    # ========================================================

    cpu_trend = safe_upper(
        cpu.get(
            "trend",
            "UNKNOWN"
        )
    )

    if cpu_trend == "DEGRADING":

        if cpu_saturation < 50:

            score += 2

            factors.append(
                "CPU trend is increasing, but absolute CPU "
                "saturation remains low."
            )

        else:

            score += 7

            factors.append(
                "CPU trend is degrading with meaningful "
                "resource utilization."
            )


    # ========================================================
    # MEMORY TREND
    # ========================================================

    memory_trend = safe_upper(
        memory.get(
            "trend",
            "UNKNOWN"
        )
    )

    if memory_trend == "DEGRADING":

        if memory_saturation < 50:

            score += 2

            factors.append(
                "Memory trend is increasing, but absolute memory "
                "saturation remains low."
            )

        else:

            score += 7

            factors.append(
                "Memory trend is degrading with meaningful "
                "resource utilization."
            )


    score = int(
        round(
            clamp(
                score,
                0,
                100
            )
        )
    )


    return {
        "score":
            score,

        "risk_level":
            get_risk_level(score),

        "score_action":
            get_score_action(score),

        "factors":
            factors
    }


# ============================================================
# ISOLATED LATENCY REGRESSION
# ============================================================

def is_isolated_latency_regression(context):

    signals = context.get(
        "signals",
        {}
    )

    latency = signals.get(
        "latency",
        {}
    )

    errors = signals.get(
        "errors",
        {}
    )

    cpu = signals.get(
        "cpu",
        {}
    )

    memory = signals.get(
        "memory",
        {}
    )

    pod_health = signals.get(
        "pod_health",
        {}
    )


    latency_change = safe_float(
        latency.get(
            "change_percent",
            0
        )
    )


    error_delta = safe_float(
        errors.get(
            "delta_percentage_points",
            0
        )
    )


    cpu_saturation = safe_float(
        cpu.get(
            "canary_saturation_percent",
            0
        )
    )


    memory_saturation = safe_float(
        memory.get(
            "canary_saturation_percent",
            0
        )
    )


    healthy = bool(
        pod_health.get(
            "healthy",
            False
        )
    )


    restarts = safe_int(
        pod_health.get(
            "restarts",
            0
        )
    )


    overall_trend = safe_upper(
        context.get(
            "overall_trend",
            "UNKNOWN"
        )
    )


    return (
        latency_change > 15
        and error_delta <= 0.5
        and cpu_saturation < 50
        and memory_saturation < 50
        and healthy
        and restarts == 0
        and overall_trend != "DEGRADING"
    )


# ============================================================
# SERIOUS RISK DOMAIN COUNT
# ============================================================

def count_serious_risk_domains(context):

    signals = context.get(
        "signals",
        {}
    )

    latency = signals.get(
        "latency",
        {}
    )

    errors = signals.get(
        "errors",
        {}
    )

    cpu = signals.get(
        "cpu",
        {}
    )

    memory = signals.get(
        "memory",
        {}
    )

    pod_health = signals.get(
        "pod_health",
        {}
    )


    count = 0


    if safe_float(
        latency.get(
            "change_percent",
            0
        )
    ) > 60:

        count += 1


    if safe_float(
        errors.get(
            "delta_percentage_points",
            0
        )
    ) > 5:

        count += 1


    if safe_float(
        cpu.get(
            "canary_saturation_percent",
            0
        )
    ) >= 85:

        count += 1


    if safe_float(
        memory.get(
            "canary_saturation_percent",
            0
        )
    ) >= 85:

        count += 1


    if (
        not bool(
            pod_health.get(
                "healthy",
                False
            )
        )
        or
        safe_int(
            pod_health.get(
                "restarts",
                0
            )
        ) > 0
    ):

        count += 1


    if safe_upper(
        context.get(
            "overall_trend",
            "UNKNOWN"
        )
    ) == "DEGRADING":

        count += 1


    return count



# ============================================================
# CLEAR HEALTHY-STATE QUALIFICATION
# ============================================================

def is_clear_healthy_state(
    context,
    telemetry_risk
):

    signals = context.get(
        "signals",
        {}
    )

    latency = signals.get(
        "latency",
        {}
    )

    errors = signals.get(
        "errors",
        {}
    )

    cpu = signals.get(
        "cpu",
        {}
    )

    memory = signals.get(
        "memory",
        {}
    )

    pod_health = signals.get(
        "pod_health",
        {}
    )

    guardrail = context.get(
        "hard_guardrail",
        {}
    )

    data_quality = context.get(
        "data_quality",
        {}
    )


    if bool(
        guardrail.get(
            "triggered",
            False
        )
    ):

        return False


    if safe_float(
        telemetry_risk.get(
            "score",
            100
        )
    ) > HEALTHY_PROMOTION_MAX_BASE_RISK:

        return False


    if safe_upper(
        telemetry_risk.get(
            "score_action",
            "PAUSE"
        )
    ) != "PROMOTE":

        return False


    if count_serious_risk_domains(
        context
    ) != 0:

        return False


    if safe_float(
        latency.get(
            "change_percent",
            0
        )
    ) > 15:

        return False


    if safe_upper(
        latency.get(
            "severity",
            "UNKNOWN"
        )
    ) not in {
        "NORMAL",
        "LOW"
    }:

        return False


    if safe_upper(
        latency.get(
            "trend",
            "UNKNOWN"
        )
    ) == "DEGRADING":

        return False


    if safe_float(
        errors.get(
            "delta_percentage_points",
            0
        )
    ) > 0.5:

        return False


    if safe_upper(
        errors.get(
            "severity",
            "UNKNOWN"
        )
    ) not in {
        "NORMAL",
        "LOW"
    }:

        return False


    if safe_upper(
        errors.get(
            "trend",
            "UNKNOWN"
        )
    ) == "DEGRADING":

        return False


    if safe_float(
        cpu.get(
            "canary_saturation_percent",
            0
        )
    ) >= 50:

        return False


    if safe_float(
        memory.get(
            "canary_saturation_percent",
            0
        )
    ) >= 50:

        return False


    if not bool(
        pod_health.get(
            "healthy",
            False
        )
    ):

        return False


    if safe_int(
        pod_health.get(
            "restarts",
            0
        )
    ) != 0:

        return False


    if safe_upper(
        context.get(
            "overall_trend",
            "UNKNOWN"
        )
    ) == "DEGRADING":

        return False


    # New median-based risk_context.py publishes this field.
    # If present, it must explicitly confirm sufficient valid windows.
    if (
        data_quality
        and
        not bool(
            data_quality.get(
                "enough_valid_windows",
                False
            )
        )
    ):

        return False


    return True



# ============================================================
# ADAPTIVE AI ANALYSIS MODE
#
# This router DOES NOT make the deployment decision.
# It only decides how much reasoning effort Qwen3 should use.
# Qwen3 remains the primary PROMOTE / PAUSE / ROLLBACK authority.
# ============================================================

def determine_analysis_mode(
    context,
    telemetry_risk
):

    signals = context.get(
        "signals",
        {}
    )

    latency = signals.get(
        "latency",
        {}
    )

    errors = signals.get(
        "errors",
        {}
    )

    cpu = signals.get(
        "cpu",
        {}
    )

    memory = signals.get(
        "memory",
        {}
    )

    pod_health = signals.get(
        "pod_health",
        {}
    )

    data_quality = context.get(
        "data_quality",
        {}
    )

    guardrail = context.get(
        "hard_guardrail",
        {}
    )


    reasons = []


    # Poor or incomplete telemetry means the situation is ambiguous.
    if (
        data_quality
        and
        not bool(
            data_quality.get(
                "enough_valid_windows",
                False
            )
        )
    ):

        reasons.append(
            "Insufficient valid telemetry windows"
        )


    excluded_windows = safe_int(
        data_quality.get(
            "excluded_windows",
            0
        ),
        0
    )


    if excluded_windows > 0:

        reasons.append(
            f"{excluded_windows} telemetry window(s) excluded"
        )


    # Any hard-safety condition deserves deeper AI interpretation
    # even though the governance barrier remains non-negotiable.
    if bool(
        guardrail.get(
            "triggered",
            False
        )
    ):

        reasons.append(
            "Hard safety guardrail is triggered"
        )


    latency_change = safe_float(
        latency.get(
            "change_percent",
            0
        )
    )


    latency_trend = safe_upper(
        latency.get(
            "trend",
            "UNKNOWN"
        )
    )


    if latency_change > 15:

        reasons.append(
            "Latency is above the normal 15% reference"
        )


    if latency_trend == "DEGRADING":

        reasons.append(
            "Latency trend is degrading"
        )


    error_delta = safe_float(
        errors.get(
            "delta_percentage_points",
            0
        )
    )


    error_trend = safe_upper(
        errors.get(
            "trend",
            "UNKNOWN"
        )
    )


    if error_delta > 0.5:

        reasons.append(
            "Canary error rate is elevated versus Stable"
        )


    if error_trend == "DEGRADING":

        reasons.append(
            "Error-rate trend is degrading"
        )


    cpu_saturation = safe_float(
        cpu.get(
            "canary_saturation_percent",
            0
        )
    )


    memory_saturation = safe_float(
        memory.get(
            "canary_saturation_percent",
            0
        )
    )


    cpu_trend = safe_upper(
        cpu.get(
            "trend",
            "UNKNOWN"
        )
    )


    memory_trend = safe_upper(
        memory.get(
            "trend",
            "UNKNOWN"
        )
    )


    # A trend by itself does not force DEEP mode when absolute
    # utilization is still very low. This keeps clear healthy
    # cases fast while preserving full resource awareness.
    if (
        cpu_saturation >= 50
        or
        (
            cpu_trend == "DEGRADING"
            and cpu_saturation >= 35
        )
    ):

        reasons.append(
            "CPU utilization requires deeper correlation"
        )


    if (
        memory_saturation >= 50
        or
        (
            memory_trend == "DEGRADING"
            and memory_saturation >= 35
        )
    ):

        reasons.append(
            "Memory utilization requires deeper correlation"
        )


    healthy = bool(
        pod_health.get(
            "healthy",
            False
        )
    )


    restarts = safe_int(
        pod_health.get(
            "restarts",
            0
        )
    )


    if not healthy:

        reasons.append(
            "Canary pod health is degraded"
        )


    if restarts > 0:

        reasons.append(
            "Canary container restarts detected"
        )


    overall_trend = safe_upper(
        context.get(
            "overall_trend",
            "UNKNOWN"
        )
    )


    if overall_trend == "DEGRADING":

        reasons.append(
            "Overall deployment trend is degrading"
        )


    serious_domains = count_serious_risk_domains(
        context
    )


    if serious_domains > 0:

        reasons.append(
            f"{serious_domains} serious risk domain(s) detected"
        )


    # Supporting score is used ONLY as a complexity hint.
    # It does not select the deployment action.
    supporting_score = safe_int(
        telemetry_risk.get(
            "score",
            0
        )
    )


    if supporting_score >= 15:

        reasons.append(
            "Supporting telemetry risk is elevated"
        )


    if reasons:

        return {
            "mode":
                "DEEP",

            "model":
                DEEP_AI_MODEL,

            "thinking_enabled":
                False,

            "num_ctx":
                AI_CONTEXT_SIZE,

            "timeout_seconds":
                DEEP_AI_TIMEOUT_SECONDS,

            "reasons":
                reasons
        }


    return {
        "mode":
            "FAST",

        "model":
            FAST_AI_MODEL,

        "thinking_enabled":
            False,

        "num_ctx":
            AI_CONTEXT_SIZE,

        "timeout_seconds":
            FAST_AI_TIMEOUT_SECONDS,

        "reasons": [
            "Signals are clear and do not require extended reasoning"
        ]
    }


# ============================================================
# AI INPUT
# ============================================================

def build_ai_input(
    context,
    telemetry_risk,
    analysis_mode=None
):

    signals = context.get(
        "signals",
        {}
    )

    observation_history = context.get(
        "observation_history",
        []
    )

    # Keep only the fields useful for contextual AI analysis.
    # This preserves all four windows without sending unnecessary
    # Kubernetes/Prometheus payload noise to the model.
    compact_history = []

    for item in observation_history:

        compact_history.append({

            "window":
                item.get(
                    "window"
                ),

            "valid":
                item.get(
                    "valid",
                    True
                ),

            "stable_latency_ms":
                item.get(
                    "stable_latency_ms"
                ),

            "canary_latency_ms":
                item.get(
                    "canary_latency_ms"
                ),

            "latency_change_percent":
                item.get(
                    "latency_change_percent"
                ),

            "stable_error_rate_percent":
                item.get(
                    "stable_error_rate_percent"
                ),

            "canary_error_rate_percent":
                item.get(
                    "canary_error_rate_percent"
                ),

            "canary_cpu_millicores":
                item.get(
                    "canary_cpu_millicores"
                ),

            "canary_cpu_saturation_percent":
                item.get(
                    "canary_cpu_saturation_percent"
                ),

            "canary_memory_mb":
                item.get(
                    "canary_memory_mb"
                ),

            "canary_memory_saturation_percent":
                item.get(
                    "canary_memory_saturation_percent"
                ),

            "healthy":
                item.get(
                    "healthy"
                ),

            "restarts":
                item.get(
                    "restarts"
                )
        })


    if analysis_mode is None:

        analysis_mode = {
            "mode":
                "UNKNOWN"
        }


    return {

        "analysis_mode":
            analysis_mode.get(
                "mode",
                "UNKNOWN"
            ),

        "analysis_model":
            analysis_mode.get(
                "model",
                DEEP_AI_MODEL
            ),

        "deployment":
            context.get(
                "deployment",
                {}
            ),

        "data_quality":
            context.get(
                "data_quality",
                {}
            ),

        "aggregation":
            context.get(
                "aggregation",
                {}
            ),

        "trend_summary":
            context.get(
                "trend_summary",
                {
                    "latency":
                        signals.get(
                            "latency",
                            {}
                        ).get(
                            "trend",
                            "UNKNOWN"
                        ),

                    "errors":
                        signals.get(
                            "errors",
                            {}
                        ).get(
                            "trend",
                            "UNKNOWN"
                        ),

                    "cpu":
                        signals.get(
                            "cpu",
                            {}
                        ).get(
                            "trend",
                            "UNKNOWN"
                        ),

                    "memory":
                        signals.get(
                            "memory",
                            {}
                        ).get(
                            "trend",
                            "UNKNOWN"
                        ),

                    "overall":
                        context.get(
                            "overall_trend",
                            "UNKNOWN"
                        )
                }
            ),

        "representative_signals":
            signals,

        "observation_history":
            compact_history,

        "deterministic_risk":
            telemetry_risk,

        "hard_guardrail":
            context.get(
                "hard_guardrail",
                {}
            ),

        "isolated_latency_regression":
            is_isolated_latency_regression(
                context
            ),

        "serious_risk_domain_count":
            count_serious_risk_domains(
                context
            ),

        "clear_healthy_state":
            is_clear_healthy_state(
                context,
                telemetry_risk
            )
    }


# ============================================================
# AI PROMPT
# ============================================================

# ============================================================
# STRUCTURED AI RESPONSE SCHEMA
# ============================================================

AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "PROMOTE",
                "PAUSE",
                "ROLLBACK"
            ]
        },
        "risk_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100
        },
        "confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100
        },
        "findings": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "items": {
                "type": "string"
            }
        },
        "correlation_summary": {
            "type": "string"
        },
        "recommendation": {
            "type": "string"
        }
    },
    "required": [
        "decision",
        "risk_score",
        "confidence",
        "findings",
        "correlation_summary",
        "recommendation"
    ],
    "additionalProperties": False
}


# ============================================================
# AI PROMPT
# ============================================================

def build_prompt(
    context,
    telemetry_risk,
    analysis_mode
):

    ai_input = build_ai_input(
        context,
        telemetry_risk,
        analysis_mode
    )


    compact_json = json.dumps(
        ai_input,
        separators=(
            ",",
            ":"
        )
    )


    schema_json = json.dumps(
        AI_RESPONSE_SCHEMA,
        separators=(
            ",",
            ":"
        )
    )


    mode_name = safe_upper(
        analysis_mode.get(
            "mode",
            "FAST"
        )
    )


    if mode_name == "DEEP":

        mode_guidance = (
            "Perform a careful multi-window and cross-domain correlation. "
            "Distinguish a truly isolated issue from multiple serious domains. "
            "Do not default to PAUSE when the evidence already demonstrates "
            "persistent severe correlated degradation."
        )

    else:

        mode_guidance = (
            "The telemetry is straightforward. Make a concise direct decision "
            "while still checking all supplied application, resource and pod signals."
        )


    return f"""
You are the PRIMARY AI decision engine for a Kubernetes Canary deployment.

Your task is to make the deployment decision:
PROMOTE, PAUSE, or ROLLBACK.

The analysis_mode field is an inference-effort hint only:
- FAST means the telemetry is straightforward; make a concise direct decision.
- DEEP means the telemetry needs deeper cross-signal correlation.
The mode does NOT tell you which deployment action to choose.

Mode-specific analysis guidance:
{mode_guidance}

Python has prepared trustworthy telemetry facts and statistical features.
The deterministic risk score is SUPPORTING EVIDENCE only. It does not make
the normal deployment decision. You must interpret the full context.

Analyze BOTH:
1. representative_signals: median/representative deployment state
2. observation_history: every live telemetry window from this deployment cycle

Evaluate all of these together:
- Stable versus Canary latency
- latency consistency across windows
- error-rate behavior
- CPU usage and absolute CPU saturation
- memory usage and absolute memory saturation
- pod readiness and health
- container restarts
- multi-window trends
- cross-signal correlation
- traffic/data quality
- deterministic risk factors as supporting evidence
- hard safety guardrail state

Deployment policy context:
- Stable is the current live baseline.
- A Canary latency increase up to approximately 15% versus Stable is normally
  acceptable unless other signals indicate meaningful correlated degradation.
- A small latency increase inside that range is not by itself a reason to PAUSE.
- Negative error delta means Canary errors are better than Stable.
- CPU or memory percentage movement is not significant when absolute resource
  saturation remains low.
- Healthy pods and low resource usage do NOT cancel a serious application-level
  regression in latency or errors.
- Treat the supplied severity and trend classifications as trusted statistical
  features produced from the observation windows.
- A DEGRADING trend increases concern, but a STABLE trend can still represent a
  persistent bad condition when the absolute severity remains high.
- Correlated degradation across independent signals is more serious than one
  isolated signal.
- "Isolated latency degradation" means latency is the only materially bad domain
  while error rate, resources, pod health and restarts remain normal.
- If serious_risk_domain_count is 2 or more, do NOT describe the condition as
  isolated merely because CPU, memory and pods are healthy.
- PAUSE is appropriate when evidence is ambiguous, data quality is insufficient,
  signals conflict, or a moderate/isolated issue needs more observation.
- ROLLBACK is appropriate when the Canary shows persistent severe or correlated
  regression across multiple independent application/operational domains.
- In particular, severe latency regression together with severe error regression
  is correlated application-level degradation and can justify ROLLBACK even when
  CPU, memory and pod health are normal.
- A hard safety guardrail is non-negotiable. The execution layer can block an
  unsafe AI action if a hard guardrail is triggered.
- The deterministic risk score is supporting evidence only. Never claim that it
  made, recommended, overrode, or enforced the deployment decision.
- Do not invent measurements or hidden policies.
- Explicitly use the multi-window history when forming findings.
- confidence is your self-assessed confidence in this decision.
- risk_score is your contextual deployment risk from 0 to 100:
  0 means minimal observed deployment risk and 100 means extreme risk.

Your explanation must be consistent with your chosen decision.

Return only JSON matching this exact schema:
{schema_json}

DEPLOYMENT DATA:
{compact_json}
""".strip()



# ============================================================
# OLLAMA MODEL WARM-UP
# ============================================================

def prewarm_ollama_model(
    model_name
):

    endpoint = (
        OLLAMA_URL.rstrip("/")
        +
        "/api/generate"
    )


    payload = {
        "model":
            model_name,

        "stream":
            False,

        "keep_alive":
            MODEL_KEEP_ALIVE
    }


    request = urllib.request.Request(

        endpoint,

        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),

        headers={
            "Content-Type":
                "application/json"
        },

        method="POST"
    )


    with urllib.request.urlopen(
        request,
        timeout=MODEL_WARMUP_TIMEOUT_SECONDS
    ) as response:

        result = json.loads(
            response.read().decode(
                "utf-8"
            )
        )


    return {
        "model":
            result.get(
                "model",
                model_name
            ),

        "load_duration":
            result.get(
                "load_duration",
                0
            )
    }


# ============================================================
# CALL OLLAMA / QWEN3
# ============================================================

def call_ollama(
    prompt,
    analysis_mode,
    timeout_seconds=None,
    force_model=None,
    force_thinking=None
):

    endpoint = (
        OLLAMA_URL.rstrip("/")
        +
        "/api/chat"
    )


    mode = safe_upper(
        analysis_mode.get(
            "mode",
            "FAST"
        )
    )


    selected_model = str(
        force_model
        or
        analysis_mode.get(
            "model",
            FAST_AI_MODEL
        )
    ).strip()


    if not selected_model:

        selected_model = FAST_AI_MODEL


    if force_thinking is None:

        thinking_enabled = bool(
            analysis_mode.get(
                "thinking_enabled",
                mode == "DEEP"
            )
        )

    else:

        thinking_enabled = bool(
            force_thinking
        )


    num_ctx = safe_int(
        analysis_mode.get(
            "num_ctx",
            AI_CONTEXT_SIZE
        ),
        AI_CONTEXT_SIZE
    )


    if timeout_seconds is None:

        timeout_seconds = safe_int(
            analysis_mode.get(
                "timeout_seconds",
                FAST_AI_TIMEOUT_SECONDS
            ),
            FAST_AI_TIMEOUT_SECONDS
        )


    payload = {
        "model":
            selected_model,

        "messages": [
            {
                "role":
                    "user",

                "content":
                    prompt
            }
        ],

        "stream":
            False,

        "think":
            thinking_enabled,

        "format":
            AI_RESPONSE_SCHEMA,

        "keep_alive":
            MODEL_KEEP_ALIVE,

        "options": {
            "temperature":
                0,

            "seed":
                42,

            "num_ctx":
                num_ctx
        }
    }


    request = urllib.request.Request(

        endpoint,

        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),

        headers={
            "Content-Type":
                "application/json"
        },

        method="POST"
    )


    with urllib.request.urlopen(
        request,
        timeout=timeout_seconds
    ) as response:

        result = json.loads(
            response.read().decode(
                "utf-8"
            )
        )


    message = result.get(
        "message",
        {}
    )


    # Qwen thinking is kept separate by Ollama.
    # The application consumes only the final structured answer.
    response_text = str(
        message.get(
            "content",
            ""
        )
    ).strip()


    return {
        "response":
            response_text,

        "model":
            result.get(
                "model",
                selected_model
            ),

        "done_reason":
            result.get(
                "done_reason",
                "UNKNOWN"
            ),

        "prompt_eval_count":
            result.get(
                "prompt_eval_count",
                0
            ),

        "eval_count":
            result.get(
                "eval_count",
                0
            ),

        "analysis_mode":
            mode,

        "selected_model":
            selected_model,

        "thinking_enabled":
            thinking_enabled,

        "num_ctx":
            num_ctx,

        "had_thinking_output":
            bool(
                message.get(
                    "thinking"
                )
            )
    }


# ============================================================
# MODEL RESPONSE VALIDATION
# ============================================================

def validate_ai_response(
    raw_ai,
    context,
    telemetry_risk
):

    if not isinstance(
        raw_ai,
        dict
    ):

        raise ValueError(
            "AI response is not a JSON object."
        )


    required = [
        "decision",
        "risk_score",
        "confidence",
        "findings",
        "correlation_summary",
        "recommendation"
    ]


    missing = [
        key
        for key in required
        if key not in raw_ai
    ]


    if missing:

        raise ValueError(
            "AI response missing required field(s): "
            +
            ", ".join(
                missing
            )
        )


    decision = safe_upper(
        raw_ai.get(
            "decision"
        )
    )


    if decision not in DECISION_PRIORITY:

        raise ValueError(
            f"Unsupported AI decision: {decision}"
        )


    risk_score = safe_int(
        raw_ai.get(
            "risk_score",
            -1
        ),
        -1
    )


    if not 0 <= risk_score <= 100:

        raise ValueError(
            "AI risk_score must be between 0 and 100."
        )


    confidence = safe_int(
        raw_ai.get(
            "confidence",
            -1
        ),
        -1
    )


    if not 0 <= confidence <= 100:

        raise ValueError(
            "AI confidence must be between 0 and 100."
        )


    findings = raw_ai.get(
        "findings"
    )


    if not isinstance(
        findings,
        list
    ):

        raise ValueError(
            "AI findings must be a list."
        )


    cleaned_findings = []


    for item in findings:

        value = str(
            item
        ).strip()


        if not value:

            continue


        # Reject numeric-only or placeholder-style output.
        if re.fullmatch(
            r"[-+]?\d+(?:\.\d+)?%?",
            value
        ):

            continue


        if value not in cleaned_findings:

            cleaned_findings.append(
                value
            )


    if not 2 <= len(
        cleaned_findings
    ) <= 6:

        raise ValueError(
            "AI must return between 2 and 6 meaningful findings."
        )


    correlation_summary = str(
        raw_ai.get(
            "correlation_summary",
            ""
        )
    ).strip()


    recommendation = str(
        raw_ai.get(
            "recommendation",
            ""
        )
    ).strip()


    if len(
        correlation_summary
    ) < 20:

        raise ValueError(
            "AI correlation summary is too weak."
        )


    if len(
        recommendation
    ) < 20:

        raise ValueError(
            "AI recommendation is too weak."
        )


    base_score = safe_int(
        telemetry_risk.get(
            "score",
            0
        )
    )


    return {
        "status":
            "AVAILABLE",

        "model":
            OLLAMA_MODEL,

        "decision":
            decision,

        "risk_score":
            risk_score,

        "risk_level":
            get_risk_level(
                risk_score
            ),

        "risk_adjustment":
            risk_score
            -
            base_score,

        "confidence":
            confidence,

        "findings":
            cleaned_findings,

        "correlation_summary":
            correlation_summary,

        "recommendation":
            recommendation,

        "fallback":
            False,

        "policy_enforced":
            False,

        "policy_notes":
            [],

        "isolated_latency_regression":
            is_isolated_latency_regression(
                context
            ),

        "serious_risk_domain_count":
            count_serious_risk_domains(
                context
            ),

        "clear_healthy_state":
            is_clear_healthy_state(
                context,
                telemetry_risk
            )
    }


# ============================================================
# AI UNAVAILABLE FAIL-SAFE
# ============================================================

def build_ai_unavailable(
    context,
    telemetry_risk,
    error
):

    base_score = safe_int(
        telemetry_risk.get(
            "score",
            0
        )
    )


    return {
        "status":
            "UNAVAILABLE",

        "model":
            OLLAMA_MODEL,

        "decision":
            "PAUSE",

        "risk_score":
            base_score,

        "risk_level":
            get_risk_level(
                base_score
            ),

        "risk_adjustment":
            0,

        "confidence":
            0,

        "findings": [
            "The AI decision engine did not return a valid deployment assessment.",
            "The rollout is being held at the current Canary weight until AI analysis is available."
        ],

        "correlation_summary":
            "No AI cross-signal correlation result is available for this analysis cycle.",

        "recommendation":
            "Keep the Canary paused and rerun the AI analysis before changing deployment traffic.",

        "fallback":
            True,

        "fallback_reason":
            str(
                error
            ),

        "policy_enforced":
            True,

        "policy_notes": [
            "AI-unavailable fail-safe prevents automatic promotion or rollback without an AI decision."
        ],

        "isolated_latency_regression":
            is_isolated_latency_regression(
                context
            ),

        "serious_risk_domain_count":
            count_serious_risk_domains(
                context
            ),

        "clear_healthy_state":
            is_clear_healthy_state(
                context,
                telemetry_risk
            )
    }


# ============================================================
# HARD SAFETY GOVERNANCE
# ============================================================

def apply_hard_safety_governance(
    context,
    ai_analysis
):

    guardrail = context.get(
        "hard_guardrail",
        {}
    )


    guardrail_triggered = bool(
        guardrail.get(
            "triggered",
            False
        )
    )


    mandatory_action = safe_upper(
        guardrail.get(
            "mandatory_action",
            guardrail.get(
                "mandatory",
                "NONE"
            )
        ),
        "NONE"
    )


    ai_status = safe_upper(
        ai_analysis.get(
            "status",
            "UNAVAILABLE"
        )
    )


    ai_decision = safe_upper(
        ai_analysis.get(
            "decision",
            "PAUSE"
        )
    )


    # AI is the normal decision authority.
    if ai_status != "AVAILABLE":

        return {
            "decision":
                "PAUSE",

            "decision_source":
                "AI_UNAVAILABLE_FAILSAFE",

            "safety_status":
                "FAILSAFE",

            "guardrail_triggered":
                guardrail_triggered,

            "guardrail_action":
                mandatory_action
        }


    # Deterministic governance acts only as the hard safety barrier.
    if (
        guardrail_triggered
        and mandatory_action in DECISION_PRIORITY
    ):

        return {
            "decision":
                mandatory_action,

            "decision_source":
                "HARD_SAFETY_GUARDRAIL",

            "safety_status":
                "BLOCKED"
                if mandatory_action != ai_decision
                else
                "ENFORCED",

            "guardrail_triggered":
                True,

            "guardrail_action":
                mandatory_action
        }


    return {
        "decision":
            ai_decision,

        "decision_source":
            "AI_DECISION_ENGINE",

        "safety_status":
            "SAFE",

        "guardrail_triggered":
            False,

        "guardrail_action":
            "NONE"
    }


# ============================================================
# FINAL ASSESSMENT
# ============================================================

def calculate_final_assessment(
    context,
    telemetry_risk,
    ai_analysis
):

    governance = apply_hard_safety_governance(
        context,
        ai_analysis
    )


    ai_status = safe_upper(
        ai_analysis.get(
            "status",
            "UNAVAILABLE"
        )
    )


    if ai_status == "AVAILABLE":

        final_risk_score = safe_int(
            ai_analysis.get(
                "risk_score",
                telemetry_risk.get(
                    "score",
                    0
                )
            )
        )

    else:

        final_risk_score = safe_int(
            telemetry_risk.get(
                "score",
                0
            )
        )


    final_risk_score = int(
        clamp(
            final_risk_score,
            0,
            100
        )
    )


    final_risk_level = get_risk_level(
        final_risk_score
    )


    return {
        "risk_score":
            final_risk_score,

        "risk_level":
            final_risk_level,

        "score_action":
            telemetry_risk.get(
                "score_action",
                "UNKNOWN"
            ),

        "ai_suggested_action":
            ai_analysis.get(
                "decision",
                "PAUSE"
            ),

        "decision":
            governance[
                "decision"
            ],

        "decision_source":
            governance[
                "decision_source"
            ],

        "safety_status":
            governance[
                "safety_status"
            ],

        "guardrail_triggered":
            governance[
                "guardrail_triggered"
            ],

        "guardrail_action":
            governance[
                "guardrail_action"
            ]
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n=========================================="
    )

    print(
        " AI DEPLOYMENT RISK ENGINE"
    )

    print(
        "=========================================="
    )


    try:

        context = load_json(
            CONTEXT_FILE
        )

    except Exception as error:

        print(
            f"\nUnable to load {CONTEXT_FILE.name}: {error}"
        )

        return


    deployment = context.get(
        "deployment",
        {}
    )


    print(
        "\nDEPLOYMENT"
    )

    print(
        "---------------------------"
    )


    print(
        f"Stable         : "
        f"{deployment.get('stable_version', 'UNKNOWN')}"
    )

    print(
        f"Canary         : "
        f"{deployment.get('canary_version', 'UNKNOWN')}"
    )

    print(
        f"Stable Weight  : "
        f"{deployment.get('stable_weight', 0)}%"
    )

    print(
        f"Canary Weight  : "
        f"{deployment.get('canary_weight', 0)}%"
    )


    # Supporting deterministic telemetry analysis.
    telemetry_risk = calculate_base_risk(
        context
    )


    print(
        "\nSUPPORTING TELEMETRY RISK"
    )

    print(
        "---------------------------"
    )

    print(
        f"Base Risk Score: "
        f"{telemetry_risk['score']}/100"
    )

    print(
        f"Risk Level     : "
        f"{telemetry_risk['risk_level']}"
    )

    print(
        "Authority      : SUPPORTING EVIDENCE"
    )


    print(
        "\nRISK FACTORS"
    )

    print(
        "---------------------------"
    )


    for factor in telemetry_risk[
        "factors"
    ]:

        print(
            f"- {factor}"
        )


    print(
        "\nAI INPUT COVERAGE"
    )

    print(
        "---------------------------"
    )


    data_quality = context.get(
        "data_quality",
        {}
    )


    print(
        f"Observation Windows : "
        f"{len(context.get('observation_history', []))}"
    )

    print(
        f"Valid Windows       : "
        f"{data_quality.get('valid_windows', 0)}/"
        f"{data_quality.get('total_windows', 0)}"
    )

    print(
        f"Aggregation         : "
        f"{context.get('aggregation', {}).get('method', 'UNKNOWN')}"
    )

    print(
        "Signals Provided    : Latency, Errors, CPU, Memory, "
        "Pod Health, Restarts, Trends"
    )

    print(
        f"FAST AI Model       : "
        f"{FAST_AI_MODEL}"
    )

    print(
        f"DEEP AI Model       : "
        f"{DEEP_AI_MODEL}"
    )

    print(
        "Analysis Strategy   : Adaptive FAST / DEEP"
    )


    analysis_mode = determine_analysis_mode(
        context,
        telemetry_risk
    )


    print(
        "\nADAPTIVE AI ANALYSIS"
    )

    print(
        "---------------------------"
    )

    print(
        f"Analysis Mode       : "
        f"{analysis_mode['mode']}"
    )

    print(
        f"Selected Model      : "
        f"{analysis_mode['model']}"
    )

    print(
        f"Thinking Enabled    : "
        f"{analysis_mode['thinking_enabled']}"
    )

    print(
        f"Context Size        : "
        f"{analysis_mode['num_ctx']}"
    )

    print(
        "Mode Reason(s)      :"
    )


    for reason in analysis_mode.get(
        "reasons",
        []
    ):

        print(
            f"- {reason}"
        )


    print(
        "\nPreparing Ollama model..."
    )


    selected_model = analysis_mode[
        "model"
    ]


    try:

        warmup = prewarm_ollama_model(
            selected_model
        )

        print(
            f"Model Warm-up       : READY ({warmup['model']})"
        )

    except Exception as warmup_error:

        print(
            f"Model Warm-up       : WARNING - {warmup_error}"
        )

        print(
            "Continuing; Ollama can still load the model during the AI request."
        )


    print(
        "\nRunning PRIMARY AI deployment analysis..."
    )


    try:

        prompt = build_prompt(
            context,
            telemetry_risk,
            analysis_mode
        )


        model_fallback_used = False
        model_fallback_reason = ""


        try:

            model_result = call_ollama(
                prompt,
                analysis_mode,
                analysis_mode[
                    "timeout_seconds"
                ]
            )


            response_text = model_result.get(
                "response",
                ""
            )


            if not response_text:

                raise ValueError(
                    "Selected model returned an empty final response."
                )


            raw_ai = json.loads(
                response_text
            )


            ai_analysis = validate_ai_response(
                raw_ai,
                context,
                telemetry_risk
            )


        except Exception as primary_model_error:

            if analysis_mode[
                "mode"
            ] == "DEEP":

                print(
                    "\nDEEP AI call did not complete on the first attempt."
                )

                print(
                    f"Retrying once with the same DEEP AI model and context: "
                    f"{primary_model_error}"
                )


                model_result = call_ollama(
                    prompt,
                    analysis_mode,
                    OLLAMA_RETRY_TIMEOUT_SECONDS
                )


                response_text = model_result.get(
                    "response",
                    ""
                )


                if not response_text:

                    raise ValueError(
                        "DEEP AI retry returned an empty final response."
                    )


                raw_ai = json.loads(
                    response_text
                )


                ai_analysis = validate_ai_response(
                    raw_ai,
                    context,
                    telemetry_risk
                )


            else:

                print(
                    "\nFAST AI call did not complete on the first attempt."
                )

                print(
                    f"Retrying once: {primary_model_error}"
                )


                model_result = call_ollama(
                    prompt,
                    analysis_mode,
                    OLLAMA_RETRY_TIMEOUT_SECONDS
                )


                response_text = model_result.get(
                    "response",
                    ""
                )


                if not response_text:

                    raise ValueError(
                        "FAST AI retry returned an empty final response."
                    )


                raw_ai = json.loads(
                    response_text
                )


                ai_analysis = validate_ai_response(
                    raw_ai,
                    context,
                    telemetry_risk
                )


        ai_analysis[
            "analysis_mode"
        ] = analysis_mode[
            "mode"
        ]


        ai_analysis[
            "model"
        ] = model_result.get(
            "model",
            analysis_mode[
                "model"
            ]
        )


        ai_analysis[
            "model_fallback_used"
        ] = model_fallback_used


        ai_analysis[
            "model_fallback_reason"
        ] = model_fallback_reason


        ai_analysis[
            "analysis_mode_reasons"
        ] = analysis_mode.get(
            "reasons",
            []
        )


        ai_analysis[
            "model_metadata"
        ] = {
            "done_reason":
                model_result.get(
                    "done_reason",
                    "UNKNOWN"
                ),

            "prompt_eval_count":
                model_result.get(
                    "prompt_eval_count",
                    0
                ),

            "eval_count":
                model_result.get(
                    "eval_count",
                    0
                ),

            "analysis_mode":
                model_result.get(
                    "analysis_mode",
                    analysis_mode[
                        "mode"
                    ]
                ),

            "selected_model":
                model_result.get(
                    "selected_model",
                    analysis_mode[
                        "model"
                    ]
                ),

            "thinking_enabled":
                model_result.get(
                    "thinking_enabled",
                    analysis_mode[
                        "thinking_enabled"
                    ]
                ),

            "thinking_exposed":
                False,

            "had_thinking_output":
                model_result.get(
                    "had_thinking_output",
                    False
                ),

            "num_ctx":
                model_result.get(
                    "num_ctx",
                    analysis_mode[
                        "num_ctx"
                    ]
                )
        }


    except Exception as error:

        print(
            "\nWARNING: PRIMARY AI analysis failed."
        )

        print(
            f"Reason: {error}"
        )


        ai_analysis = build_ai_unavailable(
            context,
            telemetry_risk,
            error
        )


        ai_analysis[
            "analysis_mode"
        ] = analysis_mode[
            "mode"
        ]


        ai_analysis[
            "model"
        ] = analysis_mode[
            "model"
        ]


        ai_analysis[
            "model_fallback_used"
        ] = False


        ai_analysis[
            "model_fallback_reason"
        ] = ""


        ai_analysis[
            "analysis_mode_reasons"
        ] = analysis_mode.get(
            "reasons",
            []
        )


        ai_analysis[
            "model_metadata"
        ] = {
            "analysis_mode":
                analysis_mode[
                    "mode"
                ],

            "thinking_enabled":
                analysis_mode[
                    "thinking_enabled"
                ],

            "thinking_exposed":
                False,

            "num_ctx":
                analysis_mode[
                    "num_ctx"
                ]
        }


    final_assessment = calculate_final_assessment(
        context,
        telemetry_risk,
        ai_analysis
    )


    # Mark safety intervention for dashboard compatibility.
    if (
        final_assessment[
            "decision_source"
        ]
        ==
        "HARD_SAFETY_GUARDRAIL"
    ):

        ai_analysis[
            "policy_enforced"
        ] = True

        ai_analysis[
            "policy_notes"
        ] = [
            "The hard safety guardrail controlled the executable action because a non-negotiable safety condition was triggered."
        ]


    print(
        "\nAI CONTEXTUAL ANALYSIS"
    )

    print(
        "---------------------------"
    )

    print(
        f"AI Status      : "
        f"{ai_analysis.get('status', 'UNKNOWN')}"
    )

    print(
        f"Analysis Mode  : "
        f"{ai_analysis.get('analysis_mode', 'UNKNOWN')}"
    )

    print(
        f"AI Model       : "
        f"{ai_analysis.get('model', analysis_mode.get('model', 'UNKNOWN'))}"
    )

    print(
        f"Model Fallback : "
        f"{'YES' if ai_analysis.get('model_fallback_used', False) else 'NO'}"
    )

    print(
        f"AI Decision    : "
        f"{ai_analysis['decision']}"
    )

    print(
        f"AI Risk Score  : "
        f"{ai_analysis['risk_score']}/100"
    )

    print(
        f"AI Risk Level  : "
        f"{ai_analysis['risk_level']}"
    )

    print(
        f"Confidence     : "
        f"{ai_analysis['confidence']}%"
    )

    print(
        f"Isolated Latency: "
        f"{ai_analysis.get('isolated_latency_regression', False)}"
    )

    print(
        f"Serious Domains : "
        f"{ai_analysis.get('serious_risk_domain_count', 0)}"
    )

    print(
        f"Healthy Feature : "
        f"{ai_analysis.get('clear_healthy_state', False)}"
    )


    print(
        "\nAI FINDINGS"
    )

    print(
        "---------------------------"
    )


    for index, finding in enumerate(
        ai_analysis[
            "findings"
        ],
        start=1
    ):

        print(
            f"{index}. {finding}"
        )


    print(
        "\nAI CORRELATION SUMMARY"
    )

    print(
        "---------------------------"
    )

    print(
        ai_analysis[
            "correlation_summary"
        ]
    )


    print(
        "\nAI RECOMMENDATION"
    )

    print(
        "---------------------------"
    )

    print(
        ai_analysis[
            "recommendation"
        ]
    )


    print(
        "\nSAFETY GOVERNANCE"
    )

    print(
        "---------------------------"
    )

    print(
        f"Guardrail      : "
        f"{'TRIGGERED' if final_assessment['guardrail_triggered'] else 'SAFE'}"
    )

    print(
        f"Safety Status  : "
        f"{final_assessment['safety_status']}"
    )

    print(
        f"Guardrail Action: "
        f"{final_assessment['guardrail_action']}"
    )


    print(
        "\n=========================================="
    )

    print(
        " FINAL DEPLOYMENT ASSESSMENT"
    )

    print(
        "=========================================="
    )

    print(
        f"Supporting Risk: "
        f"{telemetry_risk['score']}/100"
    )

    print(
        f"AI Risk        : "
        f"{final_assessment['risk_score']}/100"
    )

    print(
        f"Risk Level     : "
        f"{final_assessment['risk_level']}"
    )

    print(
        f"AI Decision    : "
        f"{final_assessment['ai_suggested_action']}"
    )

    print(
        f"Final Decision : "
        f"{final_assessment['decision']}"
    )

    print(
        f"Decision Source: "
        f"{final_assessment['decision_source']}"
    )


    output = {

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "architecture": {
            "primary_decision_authority":
                "AI",

            "ai_model":
                ai_analysis.get(
                    "model",
                    analysis_mode.get(
                        "model",
                        DEEP_AI_MODEL
                    )
                ),

            "fast_ai_model":
                FAST_AI_MODEL,

            "deep_ai_model":
                DEEP_AI_MODEL,

            "telemetry_role":
                "STATISTICAL_AND_SUPPORTING_EVIDENCE",

            "governance_role":
                "HARD_SAFETY_BARRIER",

            "analysis_routing":
                "ADAPTIVE_FAST_DEEP"
        },

        "deployment": {
            "stable_version":
                deployment.get(
                    "stable_version",
                    "UNKNOWN"
                ),

            "canary_version":
                deployment.get(
                    "canary_version",
                    "UNKNOWN"
                ),

            "stable_weight":
                deployment.get(
                    "stable_weight",
                    0
                ),

            "canary_weight":
                deployment.get(
                    "canary_weight",
                    0
                ),

            "phase":
                deployment.get(
                    "phase",
                    context.get(
                        "phase",
                        "UNKNOWN"
                    )
                )
        },

        "telemetry_risk":
            telemetry_risk,

        "ai_analysis":
            ai_analysis,

        "final_assessment":
            final_assessment,

        "guardrail":
            context.get(
                "hard_guardrail",
                {}
            )
    }


    save_json(
        OUTPUT_FILE,
        output
    )


    print(
        "\nDecision saved to:"
    )

    print(
        OUTPUT_FILE
    )


    print(
        "\n=========================================="
    )

    print(
        " AI RISK ANALYSIS COMPLETE"
    )

    print(
        "=========================================="
    )


if __name__ == "__main__":

    main()
