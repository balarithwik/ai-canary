import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

PUSHGATEWAY_URL = (
    "http://localhost:9091"
    "/metrics/job/ai-canary-intelligence"
)

BASE_DIR = Path(__file__).resolve().parent

CONTEXT_FILE = BASE_DIR / "ai_risk_context.json"
DECISION_FILE = BASE_DIR / "ai_decision.json"
METRICS_OUTPUT_FILE = BASE_DIR / "ai_metrics.prom"

CLUSTER_NAME = "ai-canary"
ENVIRONMENT_NAME = "Development"
DEFAULT_AI_MODEL = "qwen3:4b-instruct"
AI_ENGINE = "Ollama"


# ============================================================
# VISIBLE AI ANALYSIS FLOW
#
# These are explainability stages, not hidden chain-of-thought.
# ============================================================

AI_ANALYSIS_STAGES = [
    ("Discover", "Discover rollout"),
    ("Baseline", "Identify Stable and Canary"),
    ("Collect", "Collect live telemetry"),
    ("Compare", "Compare Stable vs Canary"),
    ("Trend", "Evaluate multi-window trends"),
    ("Correlate", "Correlate application and infrastructure signals"),
    ("Risk", "AI interprets contextual deployment risk"),
    ("Decide", "AI selects PROMOTE, PAUSE, or ROLLBACK"),
]


# ============================================================
# ENUMS
# ============================================================

DECISION_CODES = {
    "UNKNOWN": 0,
    "PROMOTE": 1,
    "PAUSE": 2,
    "ROLLBACK": 3
}

RISK_LEVEL_CODES = {
    "UNKNOWN": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4
}

TREND_CODES = {
    "IMPROVING": -1,
    "STABLE": 0,
    "DEGRADING": 1,
    "UNKNOWN": 2
}

SEVERITY_CODES = {
    "UNKNOWN": 0,
    "NORMAL": 1,
    "LOW": 1,
    "ELEVATED": 2,
    "MODERATE": 2,
    "HIGH": 3,
    "SEVERE": 4,
    "CRITICAL": 4
}

GUARDRAIL_ACTION_CODES = {
    "NONE": 0,
    "PROMOTE": 1,
    "PAUSE": 2,
    "ROLLBACK": 3
}


ANALYSIS_MODE_CODES = {
    "UNKNOWN": 0,
    "FAST": 1,
    "DEEP": 2
}


# ============================================================
# BASIC HELPERS
# ============================================================

def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


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

    text = str(value).strip()

    if not text:
        return default

    return text.upper()


def escape_label(value):
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace("\r", "")
    value = value.replace("\n", "\\n")
    value = value.replace('"', '\\"')
    return value


# ============================================================
# PROMETHEUS HELPERS
# ============================================================

def gauge(name, help_text, value):
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
        f"{name} {safe_float(value)}"
    ]


def info_metric(name, help_text, labels, value=1):
    label_text = ",".join(
        f'{key}="{escape_label(val)}"'
        for key, val in labels.items()
    )

    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
        f"{name}{{{label_text}}} {safe_float(value)}"
    ]


# ============================================================
# TIMESTAMP
# ============================================================

def parse_timestamp(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        pass

    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.timestamp()
    except ValueError:
        return None


def get_analysis_timestamp(context, decision):
    candidates = [
        decision.get("timestamp"),
        decision.get("analysis_timestamp"),
        context.get("timestamp"),
        context.get("analysis_timestamp"),
    ]

    for item in candidates:
        parsed = parse_timestamp(item)

        if parsed is not None:
            return parsed

    return time.time()


# ============================================================
# OBSERVATION COUNT
# ============================================================

def get_observation_count(context):
    observations = context.get("observations")

    if isinstance(observations, list):
        return len(observations)

    if context.get("observation_count") is not None:
        return safe_int(context.get("observation_count"))

    analysis = context.get("analysis", {})

    if isinstance(analysis, dict):
        if analysis.get("observation_count") is not None:
            return safe_int(analysis.get("observation_count"))

    return 0


# ============================================================
# RISK BREAKDOWN
#
# Mirrors current deterministic ai_risk_engine.py scoring.
# ============================================================

def latency_risk_score(change_percent):
    value = safe_float(change_percent)

    if value <= 15:
        return 0
    if value <= 30:
        return 15
    if value <= 60:
        return 30
    return 45


def error_risk_score(delta_pp):
    value = safe_float(delta_pp)

    if value <= 0.5:
        return 0
    if value <= 2:
        return 10
    if value <= 5:
        return 25
    return 40


def resource_saturation_score(saturation):
    value = safe_float(saturation)

    if value < 50:
        return 0
    if value < 70:
        return 5
    if value < 85:
        return 12
    return 20


def resource_trend_score(saturation, trend):
    if safe_upper(trend) != "DEGRADING":
        return 0

    if safe_float(saturation) < 50:
        return 2

    return 7


def pod_health_risk_score(healthy, restarts):
    score = 0

    if not bool(healthy):
        score += 25

    score += min(
        safe_int(restarts) * 8,
        24
    )

    return score


def trend_risk_score(overall, latency, errors):
    score = 0

    overall = safe_upper(overall)
    latency = safe_upper(latency)
    errors = safe_upper(errors)

    if overall == "DEGRADING":
        score += 12
    elif overall == "IMPROVING":
        score -= 5

    if latency == "DEGRADING":
        score += 8

    if errors == "DEGRADING":
        score += 8

    return score


def calculate_risk_breakdown(context):
    signals = context.get("signals", {})

    latency = signals.get("latency", {})
    errors = signals.get("errors", {})
    cpu = signals.get("cpu", {})
    memory = signals.get("memory", {})
    pod_health = signals.get("pod_health", {})

    latency_score = latency_risk_score(
        latency.get("change_percent", 0)
    )

    error_score = error_risk_score(
        errors.get("delta_percentage_points", 0)
    )

    cpu_score = (
        resource_saturation_score(
            cpu.get("canary_saturation_percent", 0)
        )
        +
        resource_trend_score(
            cpu.get("canary_saturation_percent", 0),
            cpu.get("trend", "UNKNOWN")
        )
    )

    memory_score = (
        resource_saturation_score(
            memory.get("canary_saturation_percent", 0)
        )
        +
        resource_trend_score(
            memory.get("canary_saturation_percent", 0),
            memory.get("trend", "UNKNOWN")
        )
    )

    pod_score = pod_health_risk_score(
        pod_health.get("healthy", False),
        pod_health.get("restarts", 0)
    )

    trend_score = trend_risk_score(
        context.get("overall_trend", "UNKNOWN"),
        latency.get("trend", "UNKNOWN"),
        errors.get("trend", "UNKNOWN")
    )

    raw_total = (
        latency_score
        + error_score
        + cpu_score
        + memory_score
        + pod_score
        + trend_score
    )

    clamped_total = max(
        0,
        min(100, raw_total)
    )

    return {
        "latency": latency_score,
        "error": error_score,
        "cpu": cpu_score,
        "memory": memory_score,
        "pod_health": pod_score,
        "trend": trend_score,
        "raw_total": raw_total,
        "clamped_total": clamped_total,
    }


# ============================================================
# SIGNAL INTELLIGENCE
# ============================================================

def calculate_signal_intelligence(context):
    signals = context.get("signals", {})

    latency = signals.get("latency", {})
    errors = signals.get("errors", {})
    cpu = signals.get("cpu", {})
    memory = signals.get("memory", {})
    pod_health = signals.get("pod_health", {})

    total_signals = 5
    adverse = 0

    if safe_float(
        latency.get("change_percent", 0)
    ) > safe_float(
        latency.get("acceptable_reference_percent", 15),
        15
    ):
        adverse += 1

    if safe_float(
        errors.get("delta_percentage_points", 0)
    ) > 0.5:
        adverse += 1

    if safe_float(
        cpu.get("canary_saturation_percent", 0)
    ) >= 50:
        adverse += 1

    if safe_float(
        memory.get("canary_saturation_percent", 0)
    ) >= 50:
        adverse += 1

    if (
        not bool(pod_health.get("healthy", False))
        or safe_int(pod_health.get("restarts", 0)) > 0
    ):
        adverse += 1

    return {
        "signal_count": total_signals,
        "adverse_signal_count": adverse,
        "adverse_signal_percent": adverse / total_signals * 100,
    }


# ============================================================
# TREND CONSISTENCY
# ============================================================

def calculate_trend_consistency(context):
    signals = context.get("signals", {})

    overall = safe_upper(
        context.get("overall_trend", "UNKNOWN")
    )

    component_trends = [
        safe_upper(
            signals.get("latency", {}).get("trend", "UNKNOWN")
        ),
        safe_upper(
            signals.get("errors", {}).get("trend", "UNKNOWN")
        ),
        safe_upper(
            signals.get("cpu", {}).get("trend", "UNKNOWN")
        ),
        safe_upper(
            signals.get("memory", {}).get("trend", "UNKNOWN")
        ),
    ]

    valid = [
        item
        for item in component_trends
        if item != "UNKNOWN"
    ]

    if not valid or overall == "UNKNOWN":
        return 0.0

    matching = sum(
        1
        for item in valid
        if item == overall
    )

    return matching / len(valid) * 100


# ============================================================
# FINDING SEMANTIC SEVERITY
#
# 1 = healthy/informational
# 2 = caution
# 3 = high concern
# 4 = critical
# ============================================================

def classify_finding_severity(text):
    value = str(text).strip().lower()

    positive_patterns = [
        "remains normal",
        "remain normal",
        "healthy",
        "far below",
        "below resource saturation",
        "no restart",
        "no pod instability",
        "within acceptable",
        "within the accepted",
        "stable with no",
    ]

    if any(item in value for item in positive_patterns):
        return 1

    critical_patterns = [
        "critical",
        "rollback",
        "unhealthy",
        "pod instability",
        "severe latency",
        "severe error",
        "failure",
    ]

    if any(item in value for item in critical_patterns):
        return 4

    high_patterns = [
        "high latency",
        "significant",
        "degrading",
        "regression",
        "saturation",
    ]

    if any(item in value for item in high_patterns):
        return 3

    caution_patterns = [
        "elevated",
        "concern",
        "investigate",
        "warning",
    ]

    if any(item in value for item in caution_patterns):
        return 2

    return 1


# ============================================================
# BUILD METRICS
# ============================================================

def build_metrics(context, decision):
    deployment = context.get("deployment", {})
    signals = context.get("signals", {})
    guardrail = context.get("hard_guardrail", {})

    assessment = decision.get("final_assessment", {})
    telemetry_risk = decision.get("telemetry_risk", {})
    ai = decision.get("ai_analysis", {})

    architecture = decision.get(
        "architecture",
        {}
    )

    ai_model = str(
        ai.get(
            "model",
            architecture.get(
                "ai_model",
                DEFAULT_AI_MODEL
            )
        )
    )

    latency = signals.get("latency", {})
    errors = signals.get("errors", {})
    cpu = signals.get("cpu", {})
    memory = signals.get("memory", {})
    pod_health = signals.get("pod_health", {})

    final_decision = safe_upper(
        assessment.get("decision")
    )

    risk_level = safe_upper(
        assessment.get("risk_level")
    )

    overall_trend = safe_upper(
        context.get("overall_trend")
    )

    decision_source = str(
        assessment.get(
            "decision_source",
            "UNKNOWN"
        )
    )

    rollout_phase = str(
        deployment.get(
            "phase",
            context.get("phase", "UNKNOWN")
        )
    )

    lines = []


    # ========================================================
    # RUNTIME / AI GOVERNANCE INFO
    # ========================================================

    lines += info_metric(
        "ai_runtime_info",
        "AI deployment intelligence runtime information",
        {
            "cluster": CLUSTER_NAME,
            "environment": ENVIRONMENT_NAME,
            "model": ai_model,
            "engine": AI_ENGINE,
        }
    )


    lines += gauge(
        "ai_governance_active",
        "Hard safety governance barrier active around the primary AI decision engine",
        1
    )


    lines += gauge(
        "ai_policy_enforced_current",
        "Hard safety governance intervened in the current analysis cycle",
        1 if ai.get("policy_enforced", False) else 0
    )


    lines += gauge(
        "ai_fallback_used",
        "AI-unavailable fail-safe used because the primary AI analysis was unavailable",
        1 if ai.get("fallback", False) else 0
    )


    ai_status = safe_upper(
        ai.get(
            "status",
            "UNAVAILABLE"
        )
    )


    lines += gauge(
        "ai_model_available",
        "Primary AI decision engine availability 1 available 0 unavailable",
        1 if ai_status == "AVAILABLE" else 0
    )


    lines += gauge(
        "ai_primary_decision_engine_active",
        "Final action came directly from the primary AI decision engine",
        1 if decision_source == "AI_DECISION_ENGINE" else 0
    )


    lines += gauge(
        "ai_hard_safety_override_current",
        "Hard safety governance overrode or enforced the executable AI action",
        1 if decision_source == "HARD_SAFETY_GUARDRAIL" else 0
    )


    lines += info_metric(
        "ai_authority_info",
        "AI decision authority and safety governance state",
        {
            "ai_status": ai_status,
            "primary_authority": "AI",
            "decision_source": decision_source,
            "safety_status": str(
                assessment.get(
                    "safety_status",
                    "UNKNOWN"
                )
            ),
        }
    )


    analysis_mode = safe_upper(
        ai.get(
            "analysis_mode",
            ai.get(
                "model_metadata",
                {}
            ).get(
                "analysis_mode",
                "UNKNOWN"
            )
        )
    )


    mode_code = ANALYSIS_MODE_CODES.get(
        analysis_mode,
        0
    )


    thinking_enabled = bool(
        ai.get(
            "model_metadata",
            {}
        ).get(
            "thinking_enabled",
            False
        )
    )


    lines += info_metric(
        "ai_analysis_mode_info",
        "Adaptive AI analysis mode used for the latest deployment decision",
        {
            "mode": analysis_mode,
            "model": ai_model,
            "thinking": (
                "enabled"
                if thinking_enabled
                else
                "disabled"
            ),
        }
    )


    lines += gauge(
        "ai_analysis_mode_code",
        "Adaptive AI analysis mode 1 FAST 2 DEEP",
        mode_code
    )


    lines += gauge(
        "ai_thinking_enabled",
        "Qwen3 extended thinking enabled for this analysis cycle",
        1 if thinking_enabled else 0
    )


    lines += gauge(
        "ai_isolated_latency_regression",
        "Current issue classified primarily as isolated latency regression",
        1 if ai.get("isolated_latency_regression", False) else 0
    )


    lines += gauge(
        "ai_serious_risk_domain_count",
        "Number of independent serious risk domains",
        ai.get("serious_risk_domain_count", 0)
    )


    # ========================================================
    # DEPLOYMENT INFO
    # ========================================================

    lines += info_metric(
        "ai_deployment_info",
        "Current AI Canary deployment information",
        {
            "stable_version": deployment.get(
                "stable_version",
                "UNKNOWN"
            ),
            "canary_version": deployment.get(
                "canary_version",
                "UNKNOWN"
            ),
            "decision": final_decision,
            "risk_level": risk_level,
            "overall_trend": overall_trend,
            "rollout_phase": rollout_phase,
        }
    )


    lines += info_metric(
        "ai_decision_info",
        "Current AI deployment decision information",
        {
            "decision": final_decision,
            "risk_level": risk_level,
            "decision_source": decision_source,
        }
    )


    # ========================================================
    # TRAFFIC
    # ========================================================

    lines += gauge(
        "ai_stable_weight_percent",
        "Stable deployment traffic weight percentage",
        deployment.get("stable_weight", 0)
    )

    lines += gauge(
        "ai_canary_weight_percent",
        "Canary deployment traffic weight percentage",
        deployment.get("canary_weight", 0)
    )


    # ========================================================
    # CORE RISK
    # ========================================================

    telemetry_score = safe_float(
        telemetry_risk.get("score", 0)
    )

    final_score = safe_float(
        assessment.get(
            "risk_score",
            telemetry_score
        )
    )

    adjustment = safe_float(
        ai.get("risk_adjustment", 0)
    )

    confidence = safe_float(
        ai.get("confidence", 0)
    )

    lines += gauge(
        "ai_telemetry_risk_score",
        "Supporting deterministic telemetry risk score",
        telemetry_score
    )

    lines += gauge(
        "ai_deployment_risk_score",
        "Final AI deployment risk score",
        final_score
    )

    lines += gauge(
        "ai_risk_adjustment",
        "AI contextual risk adjustment",
        adjustment
    )

    lines += gauge(
        "ai_base_risk_score",
        "Supporting deterministic deployment risk score",
        telemetry_score
    )

    lines += gauge(
        "ai_final_risk_score",
        "Primary AI contextual deployment risk score",
        final_score
    )

    lines += gauge(
        "ai_contextual_adjustment",
        "Contextual risk adjustment applied by AI",
        adjustment
    )

    lines += gauge(
        "ai_confidence_percent",
        "AI decision confidence percentage",
        confidence
    )


    # ========================================================
    # DECISION STATE
    # ========================================================

    decision_code = DECISION_CODES.get(
        final_decision,
        0
    )

    lines += gauge(
        "ai_decision_code",
        "Deployment decision code 1 PROMOTE 2 PAUSE 3 ROLLBACK",
        decision_code
    )


    lines.append(
        "# HELP ai_decision_state Current deployment decision state"
    )

    lines.append(
        "# TYPE ai_decision_state gauge"
    )

    for name in [
        "PROMOTE",
        "PAUSE",
        "ROLLBACK"
    ]:
        lines.append(
            "ai_decision_state"
            f'{{decision="{name}"}} '
            f"{1 if final_decision == name else 0}"
        )


    lines += gauge(
        "ai_risk_level_code",
        "Risk level code 1 LOW 2 MEDIUM 3 HIGH 4 CRITICAL",
        RISK_LEVEL_CODES.get(
            risk_level,
            0
        )
    )


    # ========================================================
    # LATENCY
    # ========================================================

    lines += gauge(
        "ai_stable_latency_ms",
        "Stable average latency milliseconds",
        latency.get("stable_ms", 0)
    )

    lines += gauge(
        "ai_canary_latency_ms",
        "Canary average latency milliseconds",
        latency.get("canary_ms", 0)
    )

    lines += gauge(
        "ai_latency_change_percent",
        "Canary latency percentage change compared with Stable",
        latency.get("change_percent", 0)
    )

    lines += gauge(
        "ai_latency_reference_percent",
        "Normal latency increase reference percentage",
        latency.get(
            "acceptable_reference_percent",
            15
        )
    )

    lines += gauge(
        "ai_latency_severity_code",
        "Latency severity code",
        SEVERITY_CODES.get(
            safe_upper(
                latency.get("severity")
            ),
            0
        )
    )


    # ========================================================
    # ERRORS
    # ========================================================

    lines += gauge(
        "ai_stable_error_rate_percent",
        "Stable error rate percentage",
        errors.get("stable_percent", 0)
    )

    lines += gauge(
        "ai_canary_error_rate_percent",
        "Canary error rate percentage",
        errors.get("canary_percent", 0)
    )

    lines += gauge(
        "ai_error_rate_delta_pp",
        "Canary minus Stable error rate percentage points",
        errors.get(
            "delta_percentage_points",
            0
        )
    )


    # ========================================================
    # CPU
    # ========================================================

    lines += gauge(
        "ai_stable_cpu_millicores",
        "Stable CPU usage per pod in millicores",
        cpu.get("stable_millicores", 0)
    )

    lines += gauge(
        "ai_canary_cpu_millicores",
        "Canary CPU usage per pod in millicores",
        cpu.get("canary_millicores", 0)
    )

    lines += gauge(
        "ai_canary_cpu_saturation_percent",
        "Canary CPU usage as percentage of limit",
        cpu.get(
            "canary_saturation_percent",
            0
        )
    )


    # ========================================================
    # MEMORY
    # ========================================================

    lines += gauge(
        "ai_stable_memory_mb",
        "Stable memory usage per pod MB",
        memory.get("stable_mb", 0)
    )

    lines += gauge(
        "ai_canary_memory_mb",
        "Canary memory usage per pod MB",
        memory.get("canary_mb", 0)
    )

    lines += gauge(
        "ai_canary_memory_saturation_percent",
        "Canary memory usage as percentage of limit",
        memory.get(
            "canary_saturation_percent",
            0
        )
    )


    # ========================================================
    # POD HEALTH
    # ========================================================

    canary_pods = safe_int(
        pod_health.get("canary_pods", 0)
    )

    ready_pods = safe_int(
        pod_health.get("ready_pods", 0)
    )

    restarts = safe_int(
        pod_health.get("restarts", 0)
    )

    healthy = bool(
        pod_health.get("healthy", False)
    )

    lines += gauge(
        "ai_canary_pods",
        "Total Canary pod count",
        canary_pods
    )

    lines += gauge(
        "ai_canary_ready_pods",
        "Ready Canary pod count",
        ready_pods
    )

    lines += gauge(
        "ai_canary_restarts",
        "Canary container restart count",
        restarts
    )

    lines += gauge(
        "ai_canary_health_status",
        "Canary health status 1 healthy 0 unhealthy",
        1 if healthy else 0
    )

    if canary_pods > 0:
        pod_health_percent = (
            ready_pods
            / canary_pods
            * 100
        )
    else:
        pod_health_percent = 0

    lines += gauge(
        "ai_canary_pod_health_percent",
        "Percentage of Canary pods Ready",
        pod_health_percent
    )


    # ========================================================
    # TRENDS
    # ========================================================

    latency_trend = safe_upper(
        latency.get("trend")
    )

    error_trend = safe_upper(
        errors.get("trend")
    )

    cpu_trend = safe_upper(
        cpu.get("trend")
    )

    memory_trend = safe_upper(
        memory.get("trend")
    )

    lines += gauge(
        "ai_overall_trend_code",
        "Overall trend -1 improving 0 stable 1 degrading",
        TREND_CODES.get(
            overall_trend,
            2
        )
    )

    lines += gauge(
        "ai_latency_trend_code",
        "Latency trend code",
        TREND_CODES.get(
            latency_trend,
            2
        )
    )

    lines += gauge(
        "ai_error_trend_code",
        "Error trend code",
        TREND_CODES.get(
            error_trend,
            2
        )
    )

    lines += gauge(
        "ai_cpu_trend_code",
        "CPU trend code",
        TREND_CODES.get(
            cpu_trend,
            2
        )
    )

    lines += gauge(
        "ai_memory_trend_code",
        "Memory trend code",
        TREND_CODES.get(
            memory_trend,
            2
        )
    )

    lines += gauge(
        "ai_trend_consistency_percent",
        "Component trends agreeing with overall trend",
        calculate_trend_consistency(
            context
        )
    )


    # ========================================================
    # RISK BREAKDOWN
    # ========================================================

    risk = calculate_risk_breakdown(
        context
    )

    lines += gauge(
        "ai_latency_risk_score",
        "Latency contribution to deterministic risk",
        risk["latency"]
    )

    lines += gauge(
        "ai_error_risk_score",
        "Error contribution to deterministic risk",
        risk["error"]
    )

    lines += gauge(
        "ai_cpu_risk_score",
        "CPU contribution to deterministic risk",
        risk["cpu"]
    )

    lines += gauge(
        "ai_memory_risk_score",
        "Memory contribution to deterministic risk",
        risk["memory"]
    )

    lines += gauge(
        "ai_pod_health_risk_score",
        "Pod health contribution to deterministic risk",
        risk["pod_health"]
    )

    lines += gauge(
        "ai_trend_risk_score",
        "Trend contribution to deterministic risk",
        risk["trend"]
    )

    lines += gauge(
        "ai_risk_breakdown_raw_total",
        "Raw deterministic risk contribution total",
        risk["raw_total"]
    )

    lines += gauge(
        "ai_risk_breakdown_total_score",
        "Reconstructed deterministic risk score",
        risk["clamped_total"]
    )

    lines += gauge(
        "ai_risk_breakdown_difference",
        "Engine telemetry score minus reconstructed score",
        telemetry_score
        -
        risk["clamped_total"]
    )


    # ========================================================
    # SIGNAL INTELLIGENCE
    # ========================================================

    signal = calculate_signal_intelligence(
        context
    )

    lines += gauge(
        "ai_signal_count",
        "Primary deployment signal categories evaluated",
        signal["signal_count"]
    )

    lines += gauge(
        "ai_adverse_signal_count",
        "Primary signals currently showing concern",
        signal["adverse_signal_count"]
    )

    lines += gauge(
        "ai_adverse_signal_percent",
        "Percentage of evaluated signals showing concern",
        signal["adverse_signal_percent"]
    )


    # ========================================================
    # OBSERVATIONS / ANALYSIS FLOW
    # ========================================================

    lines += gauge(
        "ai_observation_windows",
        "Telemetry observation windows used",
        get_observation_count(
            context
        )
    )

    lines += gauge(
        "ai_analysis_step_count",
        "Visible AI analysis stages",
        len(AI_ANALYSIS_STAGES)
    )

    lines.append(
        "# HELP ai_analysis_stage_info Visible AI deployment analysis stage"
    )

    lines.append(
        "# TYPE ai_analysis_stage_info gauge"
    )

    for index, (
        short_name,
        full_name
    ) in enumerate(
        AI_ANALYSIS_STAGES,
        start=1
    ):
        lines.append(
            "ai_analysis_stage_info"
            "{"
            f'step="{index}",'
            f'short="{escape_label(short_name)}",'
            f'stage="{escape_label(full_name)}"'
            "} 1"
        )


    # ========================================================
    # HARD GUARDRAILS
    # ========================================================

    guardrail_triggered = bool(
        guardrail.get("triggered", False)
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

    lines += gauge(
        "ai_hard_guardrail_triggered",
        "Hard safety guardrail 1 triggered 0 safe",
        1 if guardrail_triggered else 0
    )

    lines += gauge(
        "ai_guardrail_action_code",
        "Mandatory guardrail action code",
        GUARDRAIL_ACTION_CODES.get(
            mandatory_action,
            0
        )
    )


    # ========================================================
    # FINDINGS
    # ========================================================

    findings = ai.get("findings", [])

    if not isinstance(findings, list):
        findings = [str(findings)]

    lines += gauge(
        "ai_finding_count",
        "Number of AI findings generated",
        len(findings)
    )

    if findings:
        lines.append(
            "# HELP ai_finding_info Latest AI deployment finding"
        )

        lines.append(
            "# TYPE ai_finding_info gauge"
        )

        lines.append(
            "# HELP ai_finding_severity AI finding semantic severity"
        )

        lines.append(
            "# TYPE ai_finding_severity gauge"
        )

        for index, finding in enumerate(
            findings,
            start=1
        ):
            severity = classify_finding_severity(
                finding
            )

            labels = (
                f'index="{index}",'
                f'finding="{escape_label(finding)}"'
            )

            lines.append(
                f"ai_finding_info{{{labels}}} 1"
            )

            lines.append(
                f"ai_finding_severity{{{labels}}} {severity}"
            )


    # ========================================================
    # CORRELATION / RECOMMENDATION
    # ========================================================

    correlation = ai.get(
        "correlation_summary",
        "No correlation analysis available"
    )

    recommendation = ai.get(
        "recommendation",
        "No recommendation available"
    )

    lines += info_metric(
        "ai_correlation_info",
        "Latest AI correlation analysis",
        {
            "summary": correlation
        }
    )

    lines += info_metric(
        "ai_recommendation_info",
        "Latest AI recommendation",
        {
            "recommendation": recommendation,
            "decision": final_decision,
        }
    )

    # Same recommendation text, numeric value is decision code.
    # This lets Grafana color the panel while displaying the text label.
    lines += info_metric(
        "ai_recommendation_state",
        "AI recommendation with decision severity",
        {
            "recommendation": recommendation,
            "decision": final_decision,
        },
        decision_code
    )


    # ========================================================
    # TIMESTAMPS
    # ========================================================

    now = time.time()

    analysis_timestamp = get_analysis_timestamp(
        context,
        decision
    )

    lines += gauge(
        "ai_last_analysis_timestamp_seconds",
        "Unix timestamp of latest AI analysis",
        analysis_timestamp
    )

    lines += gauge(
        "ai_metrics_published_timestamp_seconds",
        "Unix timestamp when dashboard metrics were published",
        now
    )


    return "\n".join(lines) + "\n"


# ============================================================
# SAVE / PUSH
# ============================================================

def save_metric_payload(metric_text):
    METRICS_OUTPUT_FILE.write_bytes(
        metric_text.encode("utf-8")
    )


def push_metrics(metric_text):
    request = urllib.request.Request(
        PUSHGATEWAY_URL,
        data=metric_text.encode("utf-8"),
        headers={
            "Content-Type":
                "text/plain; version=0.0.4"
        },
        method="PUT"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:
            return response.status

    except urllib.error.HTTPError as error:
        print(
            "\nPushgateway returned HTTP error:"
        )
        print(
            f"Status : {error.code}"
        )

        try:
            body = error.read().decode(
                "utf-8",
                errors="replace"
            )
            print(
                f"Reason : {body}"
            )
        except Exception:
            pass

        raise


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "\n=========================================="
    )
    print(
        " AI DEPLOYMENT INTELLIGENCE"
    )
    print(
        " METRICS PUBLISHER V2"
    )
    print(
        "=========================================="
    )

    try:
        context = load_json(
            CONTEXT_FILE
        )
        decision = load_json(
            DECISION_FILE
        )
    except Exception as error:
        print(
            f"\nUnable to load AI data: {error}"
        )
        return

    try:
        metric_text = build_metrics(
            context,
            decision
        )
    except Exception as error:
        print(
            f"\nUnable to build metrics: {error}"
        )
        return

    try:
        save_metric_payload(
            metric_text
        )
        print(
            "\nMetric payload created:"
        )
        print(
            METRICS_OUTPUT_FILE
        )
    except Exception as error:
        print(
            f"\nUnable to save metric payload: {error}"
        )
        return

    print(
        "\nPublishing AI intelligence metrics..."
    )

    try:
        status = push_metrics(
            metric_text
        )
    except Exception as error:
        print(
            f"\nPushgateway publication failed: {error}"
        )
        return

    print(
        f"Pushgateway HTTP Status: {status}"
    )

    deployment = context.get(
        "deployment",
        {}
    )

    assessment = decision.get(
        "final_assessment",
        {}
    )

    telemetry = decision.get(
        "telemetry_risk",
        {}
    )

    ai = decision.get(
        "ai_analysis",
        {}
    )


    architecture = decision.get(
        "architecture",
        {}
    )


    ai_model = str(
        ai.get(
            "model",
            architecture.get(
                "ai_model",
                DEFAULT_AI_MODEL
            )
        )
    )


    risk = calculate_risk_breakdown(
        context
    )

    signal = calculate_signal_intelligence(
        context
    )

    print(
        "\nDEPLOYMENT SUMMARY"
    )
    print(
        "---------------------------"
    )
    print(
        f"Stable Version : "
        f"{deployment.get('stable_version', 'UNKNOWN')}"
    )
    print(
        f"Canary Version : "
        f"{deployment.get('canary_version', 'UNKNOWN')}"
    )
    print(
        f"Traffic Split  : "
        f"{deployment.get('stable_weight', 0)}% Stable / "
        f"{deployment.get('canary_weight', 0)}% Canary"
    )

    print(
        "\nAI DECISION SUMMARY"
    )
    print(
        "---------------------------"
    )
    print(
        f"AI Model       : {ai_model}"
    )
    print(
        f"AI Engine      : {AI_ENGINE}"
    )
    print(
        f"AI Status      : "
        f"{ai.get('status', 'UNKNOWN')}"
    )
    print(
        f"Analysis Mode  : "
        f"{ai.get('analysis_mode', 'UNKNOWN')}"
    )
    print(
        f"Thinking       : "
        f"{'ENABLED' if ai.get('model_metadata', {}).get('thinking_enabled', False) else 'DISABLED'}"
    )
    print(
        f"Supporting Risk: "
        f"{telemetry.get('score', 0)}/100"
    )
    print(
        f"AI vs Support  : "
        f"{ai.get('risk_adjustment', 0)}"
    )
    print(
        f"AI Risk        : "
        f"{assessment.get('risk_score', 0)}/100"
    )
    print(
        f"Risk Level     : "
        f"{assessment.get('risk_level', 'UNKNOWN')}"
    )
    print(
        f"AI Confidence  : "
        f"{ai.get('confidence', 0)}%"
    )
    print(
        f"Decision       : "
        f"{assessment.get('decision', 'UNKNOWN')}"
    )

    print(
        "\nAI GOVERNANCE"
    )
    print(
        "---------------------------"
    )
    print(
        "Governance     : ACTIVE"
    )
    print(
        f"Policy Applied : "
        f"{'YES' if ai.get('policy_enforced', False) else 'NO'}"
    )
    print(
        f"Fallback Used  : "
        f"{'YES' if ai.get('fallback', False) else 'NO'}"
    )
    print(
        f"Isolated Issue : "
        f"{'YES' if ai.get('isolated_latency_regression', False) else 'NO'}"
    )
    print(
        f"Serious Domains: "
        f"{ai.get('serious_risk_domain_count', 0)}"
    )
    print(
        f"Decision Source: "
        f"{assessment.get('decision_source', 'UNKNOWN')}"
    )
    print(
        f"Safety Status  : "
        f"{assessment.get('safety_status', 'UNKNOWN')}"
    )

    print(
        "\nAI RISK BREAKDOWN"
    )
    print(
        "---------------------------"
    )
    print(
        f"Latency Risk   : {risk['latency']}"
    )
    print(
        f"Error Risk     : {risk['error']}"
    )
    print(
        f"CPU Risk       : {risk['cpu']}"
    )
    print(
        f"Memory Risk    : {risk['memory']}"
    )
    print(
        f"Pod Health Risk: {risk['pod_health']}"
    )
    print(
        f"Trend Risk     : {risk['trend']}"
    )
    print(
        f"Calculated Base: {risk['clamped_total']}/100"
    )

    print(
        "\nAI SIGNAL ANALYSIS"
    )
    print(
        "---------------------------"
    )
    print(
        f"Signals Checked   : {signal['signal_count']}"
    )
    print(
        f"Signals of Concern: {signal['adverse_signal_count']}"
    )
    print(
        f"Trend Consistency : "
        f"{calculate_trend_consistency(context):.2f}%"
    )
    print(
        f"Observation Windows: "
        f"{get_observation_count(context)}"
    )
    print(
        f"Analysis Stages   : "
        f"{len(AI_ANALYSIS_STAGES)}"
    )
    print(
        f"Findings          : "
        f"{len(ai.get('findings', []))}"
    )

    print(
        "\n=========================================="
    )
    print(
        " AI INTELLIGENCE METRICS PUBLISHED"
    )
    print(
        "=========================================="
    )


if __name__ == "__main__":
    main()
