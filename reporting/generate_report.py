import argparse
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path


# ============================================================
# BASIC HELPERS
# ============================================================

def load_json(path, default=None):
    path = Path(path)

    if not path.exists():
        return {} if default is None else default

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def esc(value):
    if value is None:
        return "N/A"
    return html.escape(str(value))


def number(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def integer(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def yes_no(value):
    return "YES" if bool(value) else "NO"


def fmt_number(value, digits=2):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_duration(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "N/A"

    minutes = int(seconds // 60)
    remaining = seconds % 60

    if minutes:
        return f"{minutes}m {remaining:.1f}s"

    return f"{remaining:.1f}s"


def safe_upper(value, default="UNKNOWN"):
    text = str(value or "").strip()
    return text.upper() if text else default


def get_path(data, path, default=None):
    current = data

    for key in path.split("."):
        if not isinstance(current, dict):
            return default

        if key not in current:
            return default

        current = current[key]

    return current


def first_value(data, paths, default=None):
    for path in paths:
        value = get_path(data, path, None)

        if value is not None and value != "":
            return value

    return default


def find_recursive_value(obj, names):
    names = set(names)

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in names and value not in (None, ""):
                return value

        for value in obj.values():
            found = find_recursive_value(value, names)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = find_recursive_value(value, names)
            if found is not None:
                return found

    return None


# ============================================================
# OBSERVATION HISTORY DISCOVERY
# ============================================================

OBSERVATION_KEYS = {
    "stable_latency_ms",
    "canary_latency_ms",
    "latency_change_percent",
    "stable_error_rate",
    "canary_error_rate",
    "error_delta_pp",
    "canary_cpu_saturation_percent",
    "canary_memory_saturation_percent",
    "canary_restarts",
}


def observation_score(item):
    if not isinstance(item, dict):
        return 0

    return len(set(item.keys()) & OBSERVATION_KEYS)


def find_observation_lists(obj, found=None):
    if found is None:
        found = []

    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            score = sum(observation_score(x) for x in obj)

            if score > 0:
                found.append((score, obj))

        for item in obj:
            find_observation_lists(item, found)

    elif isinstance(obj, dict):
        for value in obj.values():
            find_observation_lists(value, found)

    return found


def discover_observations(checkpoint_dir, context):
    candidates = find_observation_lists(context)

    for path in checkpoint_dir.rglob("*.json"):
        data = load_json(path, {})

        for candidate in find_observation_lists(data):
            candidates.append(candidate)

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: (
            len(item[1]),
            item[0]
        ),
        reverse=True
    )

    return candidates[0][1]


# ============================================================
# SVG CHART HELPERS
# ============================================================

def svg_empty(message, height=220):
    return f"""
    <svg viewBox="0 0 800 {height}" class="chart-svg">
      <rect width="800" height="{height}" rx="14" class="svg-bg"/>
      <text x="400" y="{height / 2}" text-anchor="middle"
            class="svg-muted">{esc(message)}</text>
    </svg>
    """


def svg_line_chart(
    labels,
    series,
    title,
    suffix="",
    reference=None,
    height=270
):
    if not labels or not series:
        return svg_empty(f"No {title.lower()} data available.", height)

    values = []

    for _, data, _ in series:
        values.extend(
            number(v) for v in data
            if v is not None
        )

    if reference is not None:
        values.append(number(reference))

    if not values:
        return svg_empty(f"No {title.lower()} data available.", height)

    width = 800
    left = 64
    right = 26
    top = 45
    bottom = 48

    plot_w = width - left - right
    plot_h = height - top - bottom

    y_min = min(0, min(values))
    y_max = max(values)

    if y_max <= y_min:
        y_max = y_min + 1

    padding = (y_max - y_min) * 0.12
    y_max += padding

    def x_pos(index):
        if len(labels) == 1:
            return left + plot_w / 2
        return left + (plot_w * index / (len(labels) - 1))

    def y_pos(value):
        return top + plot_h - (
            (value - y_min) /
            (y_max - y_min)
        ) * plot_h

    out = [
        f'<svg viewBox="0 0 {width} {height}" class="chart-svg">',
        f'<rect width="{width}" height="{height}" rx="14" class="svg-bg"/>',
        f'<text x="{left}" y="25" class="svg-title">{esc(title)}</text>'
    ]

    # Horizontal grid
    for i in range(5):
        y_value = y_min + (y_max - y_min) * i / 4
        y = y_pos(y_value)

        out.append(
            f'<line x1="{left}" y1="{y:.2f}" '
            f'x2="{width-right}" y2="{y:.2f}" class="svg-grid"/>'
        )

        out.append(
            f'<text x="{left-9}" y="{y+4:.2f}" '
            f'text-anchor="end" class="svg-axis">'
            f'{y_value:.1f}{esc(suffix)}</text>'
        )

    # X labels
    for index, label in enumerate(labels):
        x = x_pos(index)

        out.append(
            f'<text x="{x:.2f}" y="{height-17}" '
            f'text-anchor="middle" class="svg-axis">'
            f'{esc(label)}</text>'
        )

    # Optional reference line
    if reference is not None:
        y = y_pos(number(reference))

        out.append(
            f'<line x1="{left}" y1="{y:.2f}" '
            f'x2="{width-right}" y2="{y:.2f}" '
            f'class="svg-reference"/>'
        )

        out.append(
            f'<text x="{width-right-4}" y="{y-7:.2f}" '
            f'text-anchor="end" class="svg-reference-text">'
            f'Reference {number(reference):.1f}{esc(suffix)}</text>'
        )

    # Series
    for index, (name, data, css_class) in enumerate(series):
        points = []

        for i, raw in enumerate(data):
            if raw is None:
                continue

            x = x_pos(i)
            y = y_pos(number(raw))
            points.append(f"{x:.2f},{y:.2f}")

        if points:
            out.append(
                f'<polyline points="{" ".join(points)}" '
                f'class="{css_class} svg-line"/>'
            )

        for i, raw in enumerate(data):
            if raw is None:
                continue

            x = x_pos(i)
            y = y_pos(number(raw))

            out.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" '
                f'r="4.5" class="{css_class} svg-point"/>'
            )

        legend_x = left + index * 180

        out.append(
            f'<line x1="{legend_x}" y1="39" '
            f'x2="{legend_x+22}" y2="39" '
            f'class="{css_class} svg-line"/>'
        )

        out.append(
            f'<text x="{legend_x+29}" y="43" class="svg-legend">'
            f'{esc(name)}</text>'
        )

    out.append("</svg>")
    return "".join(out)


def svg_horizontal_bars(items, title, suffix="", max_value=None):
    if not items:
        return svg_empty(f"No {title.lower()} data available.")

    width = 800
    row_h = 43
    top = 54
    height = top + row_h * len(items) + 25

    values = [max(0, number(value)) for _, value in items]

    chart_max = max_value or max(values or [1])

    if chart_max <= 0:
        chart_max = 1

    label_w = 190
    bar_left = 215
    bar_width = 480

    out = [
        f'<svg viewBox="0 0 {width} {height}" class="chart-svg">',
        f'<rect width="{width}" height="{height}" rx="14" class="svg-bg"/>',
        f'<text x="24" y="30" class="svg-title">{esc(title)}</text>'
    ]

    for i, (label, raw) in enumerate(items):
        value = max(0, number(raw))
        y = top + i * row_h

        width_value = min(
            bar_width,
            (value / chart_max) * bar_width
        )

        out.append(
            f'<text x="{label_w}" y="{y+18}" '
            f'text-anchor="end" class="svg-axis-label">'
            f'{esc(label)}</text>'
        )

        out.append(
            f'<rect x="{bar_left}" y="{y+3}" '
            f'width="{bar_width}" height="21" '
            f'rx="6" class="svg-bar-track"/>'
        )

        out.append(
            f'<rect x="{bar_left}" y="{y+3}" '
            f'width="{width_value:.2f}" height="21" '
            f'rx="6" class="svg-bar"/>'
        )

        out.append(
            f'<text x="{bar_left+bar_width+12}" '
            f'y="{y+19}" class="svg-value">'
            f'{value:.1f}{esc(suffix)}</text>'
        )

    out.append("</svg>")
    return "".join(out)


# ============================================================
# CHECKPOINT DATA
# ============================================================

def checkpoint_number(path):
    match = re.search(r"checkpoint-(\d+)", path.name)

    if not match:
        return 999

    return int(match.group(1))


def load_checkpoint(path):
    context_path = path / "ai_risk_context.json"
    decision_path = path / "ai_decision.json"

    context = load_json(context_path, {})
    decision = load_json(decision_path, {})

    # Fallback: inspect other checkpoint JSON files.
    if not context:
        for candidate in path.rglob("*.json"):
            data = load_json(candidate, {})

            if (
                isinstance(data, dict)
                and "signals" in data
                and "deployment" in data
            ):
                context = data
                break

    if not decision:
        for candidate in path.rglob("*.json"):
            data = load_json(candidate, {})

            if (
                isinstance(data, dict)
                and "final_assessment" in data
                and "ai_analysis" in data
            ):
                decision = data
                break

    observations = discover_observations(
        path,
        context
    )

    deployment = (
        decision.get("deployment")
        or context.get("deployment")
        or {}
    )

    signals = context.get("signals", {})
    ai = decision.get("ai_analysis", {})
    supporting = decision.get("telemetry_risk", {})
    final_assessment = decision.get(
        "final_assessment",
        {}
    )

    guardrail = (
        decision.get("guardrail")
        or context.get("hard_guardrail")
        or {}
    )

    checkpoint = checkpoint_number(path)

    return {
        "checkpoint": checkpoint,
        "path": path,
        "context": context,
        "decision": decision,
        "deployment": deployment,
        "signals": signals,
        "ai": ai,
        "supporting": supporting,
        "final": final_assessment,
        "guardrail": guardrail,
        "observations": observations,
    }


# ============================================================
# RISK BREAKDOWN
# ============================================================

def extract_risk_breakdown(checkpoint):
    source = checkpoint["supporting"]

    candidates = {
        "Latency Risk": [
            "latency_risk",
            "latency_score",
        ],
        "Error Risk": [
            "error_risk",
            "errors_risk",
            "error_score",
        ],
        "CPU Risk": [
            "cpu_risk",
            "cpu_score",
        ],
        "Memory Risk": [
            "memory_risk",
            "memory_score",
        ],
        "Pod Health Risk": [
            "pod_health_risk",
            "pod_risk",
        ],
        "Trend Risk": [
            "trend_risk",
            "trend_score",
        ],
    }

    output = []

    for label, names in candidates.items():
        value = find_recursive_value(
            source,
            names
        )

        if value is not None:
            output.append(
                (label, number(value))
            )

    return output


# ============================================================
# TELEMETRY NORMALIZATION
# ============================================================

def observation_series(checkpoint):
    observations = checkpoint["observations"]

    if not observations:
        signals = checkpoint["signals"]

        latency = signals.get("latency", {})
        errors = signals.get("errors", {})
        cpu = signals.get("cpu", {})
        memory = signals.get("memory", {})

        if signals:
            observations = [{
                "stable_latency_ms":
                    latency.get("stable_ms"),
                "canary_latency_ms":
                    latency.get("canary_ms"),
                "latency_change_percent":
                    latency.get("change_percent"),
                "stable_error_rate":
                    errors.get("stable_percent"),
                "canary_error_rate":
                    errors.get("canary_percent"),
                "error_delta_pp":
                    errors.get("delta_percentage_points"),
                "canary_cpu_saturation_percent":
                    cpu.get("canary_saturation_percent"),
                "canary_memory_saturation_percent":
                    memory.get("canary_saturation_percent"),
                "canary_restarts":
                    signals.get(
                        "pod_health",
                        {}
                    ).get("restarts"),
            }]

    rows = []

    for index, observation in enumerate(
        observations,
        start=1
    ):
        rows.append({
            "label":
                f"Window {index}"
                if len(observations) > 1
                else "Median",
            "stable_latency":
                observation.get("stable_latency_ms"),
            "canary_latency":
                observation.get("canary_latency_ms"),
            "latency_change":
                observation.get("latency_change_percent"),
            "stable_error":
                observation.get("stable_error_rate"),
            "canary_error":
                observation.get("canary_error_rate"),
            "error_delta":
                observation.get("error_delta_pp"),
            "cpu_sat":
                observation.get(
                    "canary_cpu_saturation_percent"
                ),
            "memory_sat":
                observation.get(
                    "canary_memory_saturation_percent"
                ),
            "restarts":
                observation.get("canary_restarts"),
        })

    return rows


# ============================================================
# STATUS / BADGE HELPERS
# ============================================================

def badge(value):
    text = safe_upper(value)

    cls = "neutral"

    if text in {
        "PASS",
        "PASSED",
        "SUCCESS",
        "HEALTHY",
        "SAFE",
        "PROMOTE",
        "AVAILABLE",
        "LOW",
        "READY",
    }:
        cls = "good"

    elif text in {
        "PAUSE",
        "MEDIUM",
        "HIGH",
        "CONCERN",
        "WARNING",
    }:
        cls = "warn"

    elif text in {
        "ROLLBACK",
        "FAILED",
        "FAIL",
        "CRITICAL",
        "ENFORCED",
        "UNHEALTHY",
    }:
        cls = "bad"

    return (
        f'<span class="badge {cls}">'
        f'{esc(text)}</span>'
    )


# ============================================================
# HTML COMPONENTS
# ============================================================

def metric_card(label, value, note=""):
    return f"""
    <div class="metric-card">
      <div class="metric-label">{esc(label)}</div>
      <div class="metric-value">{esc(value)}</div>
      <div class="metric-note">{esc(note)}</div>
    </div>
    """


def table(headers, rows):
    head = "".join(
        f"<th>{esc(x)}</th>"
        for x in headers
    )

    body = []

    for row in rows:
        body.append(
            "<tr>" +
            "".join(
                f"<td>{value}</td>"
                for value in row
            ) +
            "</tr>"
        )

    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
    </div>
    """


# ============================================================
# REPORT BUILD
# ============================================================

def build_report(project_root, output_path):
    runtime = project_root / "runtime"
    stages_dir = runtime / "stages"

    timeline = load_json(
        runtime / "pipeline-timeline.json",
        {}
    )

    scenario_state = load_json(
        runtime / "scenario-state.json",
        {}
    )

    build_info = load_json(
        runtime / "build-info.json",
        {}
    )

    final_validation = load_json(
        runtime / "final-validation.json",
        {}
    )

    pipeline_config = load_json(
        project_root /
        "jenkins" /
        "config" /
        "pipeline-config.json",
        {}
    )

    checkpoints = []

    if stages_dir.exists():
        checkpoint_dirs = sorted(
            [
                x for x in stages_dir.iterdir()
                if x.is_dir()
                and x.name.startswith("checkpoint-")
            ],
            key=checkpoint_number
        )

        for checkpoint_dir in checkpoint_dirs:
            checkpoints.append(
                load_checkpoint(checkpoint_dir)
            )

    latest = (
        checkpoints[-1]
        if checkpoints
        else {
            "deployment": {},
            "signals": {},
            "ai": {},
            "supporting": {},
            "final": {},
            "guardrail": {},
            "observations": [],
            "checkpoint": "N/A",
        }
    )

    deployment = latest["deployment"]
    signals = latest["signals"]
    ai = latest["ai"]
    final = latest["final"]
    guardrail = latest["guardrail"]
    supporting = latest["supporting"]

    scenario = (
        scenario_state.get("scenario")
        or os.getenv("DEMO_SCENARIO")
        or "UNKNOWN"
    )

    stable_version = (
        scenario_state.get("stable_version")
        or deployment.get("stable_version")
        or build_info.get("stable_version")
        or "UNKNOWN"
    )

    canary_version = (
        scenario_state.get("canary_version")
        or deployment.get("canary_version")
        or build_info.get("canary_version")
        or "UNKNOWN"
    )

    decision = (
        final.get("decision")
        or ai.get("decision")
        or scenario_state.get("ai_decision")
        or "UNKNOWN"
    )

    decision_source = (
        final.get("decision_source")
        or scenario_state.get("decision_source")
        or "UNKNOWN"
    )

    ai_risk = (
        final.get("risk_score")
        if final.get("risk_score") is not None
        else scenario_state.get("ai_risk_score")
    )

    if ai_risk is None:
        ai_risk = supporting.get("score", 0)

    supporting_risk = supporting.get(
        "score",
        0
    )

    risk_level = (
        final.get("risk_level")
        or scenario_state.get("risk_level")
        or "UNKNOWN"
    )

    confidence = ai.get(
        "confidence",
        scenario_state.get("ai_confidence", 0)
    )

    guardrail_triggered = bool(
        guardrail.get(
            "triggered",
            False
        )
    )

    safety_status = (
        "ENFORCED"
        if guardrail_triggered
        else "SAFE"
    )

    ai_model = (
        find_recursive_value(
            latest["decision"],
            {
                "ai_model",
                "model",
                "selected_model",
            }
        )
        or get_path(
            pipeline_config,
            "ai.model",
            "UNKNOWN"
        )
    )

    analysis_mode = (
        find_recursive_value(
            latest["decision"],
            {
                "analysis_mode",
                "mode",
            }
        )
        or "UNKNOWN"
    )

    ai_status = (
        "UNAVAILABLE"
        if ai.get("fallback")
        else "AVAILABLE"
    )

    # ========================================================
    # TIMELINE
    # ========================================================

    timeline_rows = []
    duration_items = []

    for stage in timeline.get("stages", []):
        stage_name = stage.get(
            "name",
            "Unknown Stage"
        )

        duration = stage.get(
            "duration_seconds",
            0
        )

        timeline_rows.append([
            esc(stage_name),
            esc(stage.get("start_time", "N/A")),
            esc(stage.get("end_time", "N/A")),
            esc(fmt_duration(duration)),
            badge(stage.get("status", "UNKNOWN")),
        ])

        if duration is not None:
            duration_items.append(
                (
                    stage_name,
                    number(duration)
                )
            )

    # ========================================================
    # CHECKPOINT SUMMARY
    # ========================================================

    checkpoint_rows = []
    checkpoint_risk_items = []

    for cp in checkpoints:
        cp_final = cp["final"]
        cp_ai = cp["ai"]
        cp_support = cp["supporting"]
        cp_guardrail = cp["guardrail"]
        cp_signals = cp["signals"]

        latency = cp_signals.get(
            "latency",
            {}
        )

        errors = cp_signals.get(
            "errors",
            {}
        )

        cp_risk = cp_final.get(
            "risk_score",
            cp_support.get("score", 0)
        )

        checkpoint_rows.append([
            esc(f'{cp["checkpoint"]}%'),
            esc(
                fmt_number(
                    latency.get("stable_ms")
                ) + " ms"
            ),
            esc(
                fmt_number(
                    latency.get("canary_ms")
                ) + " ms"
            ),
            esc(
                fmt_number(
                    latency.get("change_percent")
                ) + "%"
            ),
            esc(
                fmt_number(
                    errors.get("canary_percent")
                ) + "%"
            ),
            esc(
                f'{number(cp_support.get("score", 0)):.0f}/100'
            ),
            esc(
                f'{number(cp_risk):.0f}/100'
            ),
            badge(
                cp_ai.get(
                    "decision",
                    cp_final.get(
                        "decision",
                        "UNKNOWN"
                    )
                )
            ),
            badge(
                cp_final.get(
                    "decision_source",
                    "UNKNOWN"
                )
            ),
            badge(
                "ENFORCED"
                if cp_guardrail.get("triggered")
                else "SAFE"
            ),
        ])

        checkpoint_risk_items.append(
            (
                f'{cp["checkpoint"]}% AI Risk',
                number(cp_risk)
            )
        )

    # ========================================================
    # LATEST TELEMETRY
    # ========================================================

    telemetry_rows = observation_series(
        latest
    )

    observation_table_rows = []

    for row in telemetry_rows:
        observation_table_rows.append([
            esc(row["label"]),
            esc(fmt_number(row["stable_latency"]) + " ms"),
            esc(fmt_number(row["canary_latency"]) + " ms"),
            esc(fmt_number(row["latency_change"]) + "%"),
            esc(fmt_number(row["stable_error"]) + "%"),
            esc(fmt_number(row["canary_error"]) + "%"),
            esc(fmt_number(row["cpu_sat"]) + "%"),
            esc(fmt_number(row["memory_sat"]) + "%"),
            esc(integer(row["restarts"])),
        ])

    labels = [
        row["label"]
        for row in telemetry_rows
    ]

    latency_chart = svg_line_chart(
        labels,
        [
            (
                "Stable",
                [
                    row["stable_latency"]
                    for row in telemetry_rows
                ],
                "series-stable"
            ),
            (
                "Canary",
                [
                    row["canary_latency"]
                    for row in telemetry_rows
                ],
                "series-canary"
            ),
        ],
        "Stable vs Canary latency",
        suffix=" ms"
    )

    error_chart = svg_line_chart(
        labels,
        [
            (
                "Stable",
                [
                    row["stable_error"]
                    for row in telemetry_rows
                ],
                "series-stable"
            ),
            (
                "Canary",
                [
                    row["canary_error"]
                    for row in telemetry_rows
                ],
                "series-canary"
            ),
        ],
        "Stable vs Canary error rate",
        suffix="%"
    )

    resource_chart = svg_line_chart(
        labels,
        [
            (
                "CPU saturation",
                [
                    row["cpu_sat"]
                    for row in telemetry_rows
                ],
                "series-cpu"
            ),
            (
                "Memory saturation",
                [
                    row["memory_sat"]
                    for row in telemetry_rows
                ],
                "series-memory"
            ),
        ],
        "Canary resource saturation",
        suffix="%",
        reference=95
    )

    risk_breakdown = extract_risk_breakdown(
        latest
    )

    risk_chart = svg_horizontal_bars(
        risk_breakdown,
        "Supporting risk breakdown",
        max_value=100
    )

    stage_chart = svg_horizontal_bars(
        duration_items,
        "Pipeline stage duration",
        suffix="s"
    )

    risk_progression_chart = svg_horizontal_bars(
        checkpoint_risk_items,
        "AI risk progression",
        max_value=100
    )

    # ========================================================
    # AI FINDINGS
    # ========================================================

    findings = ai.get(
        "findings",
        []
    )

    if not isinstance(findings, list):
        findings = [str(findings)]

    findings_html = "".join(
        f"<li>{esc(item)}</li>"
        for item in findings
        if str(item).strip()
    )

    if not findings_html:
        findings_html = (
            "<li>No AI findings were captured.</li>"
        )

    correlation = ai.get(
        "correlation_summary",
        "No correlation summary was captured."
    )

    recommendation = ai.get(
        "recommendation",
        "No AI recommendation was captured."
    )

    # ========================================================
    # QUALITY ASSESSMENT
    # ========================================================

    latency_signal = signals.get(
        "latency",
        {}
    )

    error_signal = signals.get(
        "errors",
        {}
    )

    cpu_signal = signals.get(
        "cpu",
        {}
    )

    memory_signal = signals.get(
        "memory",
        {}
    )

    pod_signal = signals.get(
        "pod_health",
        {}
    )

    perf_status = (
        "PASS"
        if number(
            latency_signal.get(
                "change_percent",
                0
            )
        ) <= 15
        else "CONCERN"
    )

    reliability_status = (
        "PASS"
        if safe_upper(
            error_signal.get(
                "severity",
                "NORMAL"
            )
        ) in {
            "NORMAL",
            "LOW"
        }
        else "CONCERN"
    )

    infrastructure_status = (
        "PASS"
        if (
            number(
                cpu_signal.get(
                    "canary_saturation_percent",
                    0
                )
            ) < 85
            and
            number(
                memory_signal.get(
                    "canary_saturation_percent",
                    0
                )
            ) < 85
        )
        else "CONCERN"
    )

    pod_status = (
        "PASS"
        if pod_signal.get(
            "healthy",
            True
        )
        else "CONCERN"
    )

    requested_windows = integer(
        get_path(
            pipeline_config,
            "ai.observation_windows",
            len(telemetry_rows)
        )
    )

    valid_windows = len(
        latest.get(
            "observations",
            []
        )
    )

    if valid_windows == 0 and telemetry_rows:
        valid_windows = 1

    data_quality = (
        "PASS"
        if (
            requested_windows > 0
            and valid_windows >= requested_windows
        )
        else "CONCERN"
    )

    quality_rows = [
        [
            "Application Performance",
            badge(perf_status),
            esc(
                f'Latency change: '
                f'{fmt_number(latency_signal.get("change_percent"))}%'
            )
        ],
        [
            "Application Reliability",
            badge(reliability_status),
            esc(
                f'Canary error rate: '
                f'{fmt_number(error_signal.get("canary_percent"))}%'
            )
        ],
        [
            "Infrastructure Health",
            badge(infrastructure_status),
            esc(
                f'CPU {fmt_number(cpu_signal.get("canary_saturation_percent"))}% / '
                f'Memory {fmt_number(memory_signal.get("canary_saturation_percent"))}%'
            )
        ],
        [
            "Pod Health",
            badge(pod_status),
            esc(
                f'Ready {pod_signal.get("ready_pods", "N/A")} / '
                f'Pods {pod_signal.get("canary_pods", "N/A")} / '
                f'Restarts {pod_signal.get("restarts", "N/A")}'
            )
        ],
        [
            "AI Availability",
            badge(
                "PASS"
                if ai_status == "AVAILABLE"
                else "CONCERN"
            ),
            esc(ai_status)
        ],
        [
            "Observation Quality",
            badge(data_quality),
            esc(
                f'{valid_windows}/{requested_windows} windows available'
            )
        ],
        [
            "Safety Governance",
            badge(safety_status),
            esc(
                guardrail.get(
                    "reason",
                    "No catastrophic condition detected"
                )
            )
        ],
    ]

    # ========================================================
    # FINAL VALIDATION
    # ========================================================

    final_rollout_status = (
        final_validation.get("rollout_status")
        or final_validation.get("status")
        or scenario_state.get("rollout_phase")
        or "UNKNOWN"
    )

    final_version = (
        final_validation.get("active_version")
        or final_validation.get("final_version")
        or scenario_state.get("final_version")
        or "UNKNOWN"
    )

    final_stable_weight = (
        final_validation.get("stable_weight")
        if final_validation.get("stable_weight") is not None
        else scenario_state.get("stable_weight")
    )

    final_canary_weight = (
        final_validation.get("canary_weight")
        if final_validation.get("canary_weight") is not None
        else scenario_state.get("canary_weight")
    )

    application_health = (
        final_validation.get("application_health")
        or final_validation.get("health")
        or "UNKNOWN"
    )

    ready_pods = final_validation.get(
        "ready_pods",
        "N/A"
    )

    desired_pods = final_validation.get(
        "desired_pods",
        "N/A"
    )

    # ========================================================
    # CONCLUSION
    # ========================================================

    if safe_upper(decision) == "ROLLBACK":
        conclusion = (
            "The AI identified material deployment risk and selected "
            "ROLLBACK based on the observed Stable-versus-Canary telemetry. "
            "The final validation section records the resulting application "
            "state and recovery verification."
        )

    elif safe_upper(decision) == "PROMOTE":
        conclusion = (
            "The AI assessed the Canary telemetry as acceptable and selected "
            "PROMOTE. The final validation section records the resulting "
            "healthy application state."
        )

    elif safe_upper(decision) == "PAUSE":
        conclusion = (
            "The AI selected PAUSE because the observed telemetry required "
            "additional investigation before further rollout progression."
        )

    else:
        conclusion = (
            "The report contains the telemetry and deployment evidence "
            "captured during this execution."
        )

    # ========================================================
    # REPORT HEADER
    # ========================================================

    generated_at = datetime.now().astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %z"
    )

    build_number = (
        os.getenv("BUILD_NUMBER")
        or build_info.get("build_number")
        or "LOCAL"
    )

    total_duration = timeline.get(
        "total_duration_sec"
    )

    total_duration_text = (
        fmt_duration(total_duration)
        if total_duration is not None
        else "N/A"
    )

    # ========================================================
    # HTML
    # ========================================================

    html_output = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>AI Canary Deployment Report</title>

<style>
:root {{
    --bg: #08111f;
    --panel: #101c2d;
    --panel2: #142337;
    --border: #263a53;
    --text: #e8eef7;
    --muted: #96a8be;
    --accent: #5aa9ff;
    --cyan: #3ddbd9;
    --good: #4cd97b;
    --warn: #ffbf47;
    --bad: #ff6b6b;
    --purple: #b18cff;
}}

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    background:
        radial-gradient(circle at top right, #163a5e 0, transparent 28%),
        radial-gradient(circle at top left, #142b4a 0, transparent 24%),
        var(--bg);
    color: var(--text);
    font-family:
        Inter,
        Segoe UI,
        Arial,
        sans-serif;
    line-height: 1.5;
}}

.container {{
    max-width: 1480px;
    margin: 0 auto;
    padding: 34px;
}}

.hero {{
    border: 1px solid var(--border);
    background:
        linear-gradient(
            135deg,
            rgba(35, 93, 145, 0.45),
            rgba(16, 28, 45, 0.94)
        );
    border-radius: 22px;
    padding: 34px;
    margin-bottom: 24px;
    box-shadow: 0 20px 60px rgba(0,0,0,.28);
}}

.hero h1 {{
    margin: 0;
    font-size: 34px;
    letter-spacing: -0.8px;
}}

.hero-sub {{
    margin-top: 8px;
    color: var(--muted);
    font-size: 15px;
}}

.section {{
    margin: 25px 0;
}}

.section-title {{
    font-size: 21px;
    margin: 0 0 14px;
}}

.section-sub {{
    color: var(--muted);
    margin: -7px 0 16px;
}}

.grid {{
    display: grid;
    gap: 14px;
}}

.grid-2 {{
    grid-template-columns: repeat(2, minmax(0,1fr));
}}

.grid-3 {{
    grid-template-columns: repeat(3, minmax(0,1fr));
}}

.grid-4 {{
    grid-template-columns: repeat(4, minmax(0,1fr));
}}

.metric-card,
.panel {{
    border: 1px solid var(--border);
    background:
        linear-gradient(
            180deg,
            rgba(20,35,55,.94),
            rgba(14,27,44,.96)
        );
    border-radius: 16px;
    box-shadow: 0 10px 30px rgba(0,0,0,.17);
}}

.metric-card {{
    padding: 17px;
    min-height: 108px;
}}

.metric-label {{
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: .7px;
    font-size: 11px;
    font-weight: 700;
}}

.metric-value {{
    font-size: 23px;
    font-weight: 750;
    margin-top: 9px;
    word-break: break-word;
}}

.metric-note {{
    color: var(--muted);
    font-size: 12px;
    margin-top: 5px;
}}

.panel {{
    padding: 20px;
}}

.panel h3 {{
    margin: 0 0 12px;
    font-size: 17px;
}}

.badge {{
    display: inline-block;
    padding: 4px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 750;
    letter-spacing: .4px;
    border: 1px solid transparent;
}}

.badge.good {{
    color: #9af0b8;
    background: rgba(76,217,123,.10);
    border-color: rgba(76,217,123,.32);
}}

.badge.warn {{
    color: #ffd47d;
    background: rgba(255,191,71,.10);
    border-color: rgba(255,191,71,.32);
}}

.badge.bad {{
    color: #ff9e9e;
    background: rgba(255,107,107,.10);
    border-color: rgba(255,107,107,.35);
}}

.badge.neutral {{
    color: #c9d6e5;
    background: rgba(150,168,190,.10);
    border-color: rgba(150,168,190,.25);
}}

.table-wrap {{
    overflow-x: auto;
    border-radius: 14px;
    border: 1px solid var(--border);
}}

table {{
    width: 100%;
    border-collapse: collapse;
    min-width: 850px;
    background: rgba(10,20,34,.42);
}}

th {{
    text-align: left;
    padding: 12px;
    color: #aac0d8;
    background: rgba(22,42,67,.9);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .55px;
}}

td {{
    padding: 12px;
    border-top: 1px solid rgba(38,58,83,.72);
    font-size: 13px;
}}

tr:hover td {{
    background: rgba(90,169,255,.035);
}}

.chart-svg {{
    width: 100%;
    height: auto;
}}

.svg-bg {{
    fill: #0d1929;
    stroke: #263a53;
}}

.svg-title {{
    fill: #dce8f6;
    font-size: 15px;
    font-weight: 700;
}}

.svg-muted {{
    fill: #7f93aa;
    font-size: 14px;
}}

.svg-grid {{
    stroke: #25374d;
    stroke-width: 1;
}}

.svg-axis {{
    fill: #869bb3;
    font-size: 10px;
}}

.svg-axis-label {{
    fill: #a9bbcf;
    font-size: 11px;
}}

.svg-value {{
    fill: #d9e5f3;
    font-size: 11px;
}}

.svg-legend {{
    fill: #b5c5d8;
    font-size: 10px;
}}

.svg-line {{
    fill: none;
    stroke-width: 2.6;
}}

.svg-point {{
    stroke-width: 2;
}}

.series-stable {{
    stroke: #5aa9ff;
    fill: #5aa9ff;
}}

.series-canary {{
    stroke: #ffbf47;
    fill: #ffbf47;
}}

.series-cpu {{
    stroke: #3ddbd9;
    fill: #3ddbd9;
}}

.series-memory {{
    stroke: #b18cff;
    fill: #b18cff;
}}

.svg-reference {{
    stroke: #ff6b6b;
    stroke-width: 1.5;
    stroke-dasharray: 7 5;
}}

.svg-reference-text {{
    fill: #ff8f8f;
    font-size: 10px;
}}

.svg-bar-track {{
    fill: #1b2b40;
}}

.svg-bar {{
    fill: #5aa9ff;
}}

.ai-box {{
    border-left: 4px solid var(--accent);
    padding: 16px 18px;
    background: rgba(90,169,255,.06);
    border-radius: 10px;
}}

.recommendation-box {{
    border-left: 4px solid var(--purple);
    padding: 16px 18px;
    background: rgba(177,140,255,.06);
    border-radius: 10px;
}}

ul.findings {{
    margin: 0;
    padding-left: 21px;
}}

ul.findings li {{
    margin: 8px 0;
}}

.footer {{
    text-align: center;
    color: var(--muted);
    font-size: 11px;
    margin: 35px 0 8px;
}}

.note {{
    color: var(--muted);
    font-size: 12px;
}}

@media(max-width: 900px) {{
    .container {{
        padding: 16px;
    }}

    .grid-2,
    .grid-3,
    .grid-4 {{
        grid-template-columns: 1fr;
    }}
}}

@media print {{
    body {{
        background: white;
        color: #1c2735;
    }}

    .metric-card,
    .panel,
    .hero {{
        box-shadow: none;
        break-inside: avoid;
    }}
}}
</style>
</head>

<body>
<div class="container">

<section class="hero">
  <h1>AI-Enabled Canary Deployment Report</h1>
  <div class="hero-sub">
    Kubernetes • Argo Rollouts • Prometheus • Grafana • Ollama AI
  </div>
</section>

<section class="section">
  <h2 class="section-title">1. Executive Summary</h2>

  <div class="grid grid-4">

    {metric_card("Scenario", scenario)}
    {metric_card("Build", build_number)}
    {metric_card("Total Duration", total_duration_text)}
    {metric_card("Final Decision", safe_upper(decision))}

    {metric_card("AI Risk Score", f"{number(ai_risk):.0f}/100")}
    {metric_card(
        "AI Confidence",
        f"{number(confidence):.0f}%",
        "Model self-reported confidence"
    )}
    {metric_card("Risk Level", safe_upper(risk_level))}
    {metric_card("Decision Source", decision_source)}

    {metric_card("Stable Version", stable_version)}
    {metric_card("Canary Version", canary_version)}
    {metric_card("Safety Governance", safety_status)}
    {metric_card("AI Model", ai_model)}

  </div>
</section>

<section class="section">
  <h2 class="section-title">2. Pipeline Execution Timeline</h2>
  <p class="section-sub">
    Real wall-clock timing captured around each visible Jenkins stage.
  </p>

  <div class="panel">
    {stage_chart}
  </div>

  <div style="height:14px"></div>

  {
      table(
          [
              "Stage",
              "Start Time",
              "End Time",
              "Duration",
              "Status"
          ],
          timeline_rows
      )
      if timeline_rows
      else '<div class="panel">Timeline data has not been captured yet.</div>'
  }
</section>

<section class="section">
  <h2 class="section-title">3. Deployment Progression</h2>

  <div class="panel">
    {risk_progression_chart}
  </div>

  <div style="height:14px"></div>

  {
      table(
          [
              "Checkpoint",
              "Stable Latency",
              "Canary Latency",
              "Latency Change",
              "Canary Error",
              "Supporting Risk",
              "AI Risk",
              "AI Decision",
              "Decision Source",
              "Safety"
          ],
          checkpoint_rows
      )
      if checkpoint_rows
      else '<div class="panel">Checkpoint evidence has not been captured yet.</div>'
  }
</section>

<section class="section">
  <h2 class="section-title">4. Multi-Window Live Telemetry History</h2>
  <p class="section-sub">
    Latest AI checkpoint telemetry. Median values are used by the risk context;
    individual windows are shown when available.
  </p>

  {
      table(
          [
              "Observation",
              "Stable Latency",
              "Canary Latency",
              "Latency Change",
              "Stable Error",
              "Canary Error",
              "CPU Saturation",
              "Memory Saturation",
              "Restarts"
          ],
          observation_table_rows
      )
      if observation_table_rows
      else '<div class="panel">No telemetry observation history is available.</div>'
  }

  <div style="height:16px"></div>

  <div class="grid grid-2">
    <div class="panel">{latency_chart}</div>
    <div class="panel">{error_chart}</div>
  </div>

  <div style="height:16px"></div>

  <div class="grid grid-2">
    <div class="panel">{resource_chart}</div>
    <div class="panel">{risk_chart}</div>
  </div>
</section>

<section class="section">
  <h2 class="section-title">5. AI Deployment Intelligence</h2>

  <div class="grid grid-4">
    {metric_card("AI Status", ai_status)}
    {metric_card("Analysis Mode", analysis_mode)}
    {metric_card("Supporting Risk", f"{number(supporting_risk):.0f}/100")}
    {metric_card("Serious Domains", ai.get("serious_risk_domain_count", "N/A"))}
  </div>

  <div style="height:16px"></div>

  <div class="grid grid-2">

    <div class="panel">
      <h3>AI Findings</h3>
      <ul class="findings">
        {findings_html}
      </ul>
    </div>

    <div class="panel">
      <h3>Decision Context</h3>

      <p>
        <strong>AI Decision:</strong>
        {badge(ai.get("decision", decision))}
      </p>

      <p>
        <strong>Final Decision:</strong>
        {badge(decision)}
      </p>

      <p>
        <strong>Decision Source:</strong>
        {badge(decision_source)}
      </p>

      <p>
        <strong>Safety Governance:</strong>
        {badge(safety_status)}
      </p>
    </div>

  </div>

  <div style="height:16px"></div>

  <div class="panel">
    <h3>AI Correlation Summary</h3>
    <div class="ai-box">
      {esc(correlation)}
    </div>
  </div>

  <div style="height:16px"></div>

  <div class="panel">
    <h3>AI Recommendation</h3>
    <div class="recommendation-box">
      {esc(recommendation)}
    </div>
  </div>

  <p class="note">
    AI Confidence is the model's self-reported confidence for this analysis;
    it is not a statistically calibrated probability.
  </p>
</section>

<section class="section">
  <h2 class="section-title">6. Final Deployment Validation</h2>

  <div class="grid grid-4">
    {metric_card("Rollout State", final_rollout_status)}
    {metric_card("Active Version", final_version)}
    {metric_card(
        "Stable Traffic",
        (
            f"{final_stable_weight}%"
            if final_stable_weight is not None
            else "N/A"
        )
    )}
    {metric_card(
        "Canary Traffic",
        (
            f"{final_canary_weight}%"
            if final_canary_weight is not None
            else "N/A"
        )
    )}

    {metric_card("Ready Pods", f"{ready_pods}/{desired_pods}")}
    {metric_card("Application Health", application_health)}
    {metric_card(
        "Rejected Canary Active",
        final_validation.get(
            "canary_active",
            "N/A"
        )
    )}
    {metric_card(
        "Recovery Verified",
        final_validation.get(
            "recovery_verified",
            "N/A"
        )
    )}
  </div>
</section>

<section class="section">
  <h2 class="section-title">7. Deployment Quality Assessment</h2>

  {
      table(
          [
              "Domain",
              "Assessment",
              "Evidence"
          ],
          quality_rows
      )
  }
</section>

<section class="section">
  <h2 class="section-title">8. Observation Quality</h2>

  <div class="grid grid-4">
    {metric_card("Requested Windows", requested_windows)}
    {metric_card("Valid Windows", valid_windows)}
    {metric_card(
        "Aggregation",
        (
            find_recursive_value(
                latest["context"],
                {
                    "aggregation_method",
                    "method",
                }
            )
            or "MEDIAN"
        )
    )}
    {metric_card(
        "Overall Trend",
        latest["context"].get(
            "overall_trend",
            "UNKNOWN"
        )
    )}
  </div>
</section>

<section class="section">
  <h2 class="section-title">9. Environment Details</h2>

  <div class="grid grid-3">

    {metric_card(
        "Cluster",
        get_path(
            pipeline_config,
            "project.cluster_name",
            "ai-canary"
        )
    )}

    {metric_card(
        "Namespace",
        get_path(
            pipeline_config,
            "project.namespace",
            "ai-canary"
        )
    )}

    {metric_card(
        "Rollout",
        get_path(
            pipeline_config,
            "project.rollout_name",
            "ai-canary-demo"
        )
    )}

    {metric_card(
        "Observation Windows",
        get_path(
            pipeline_config,
            "ai.observation_windows",
            4
        )
    )}

    {metric_card(
        "Observation Interval",
        str(
            get_path(
                pipeline_config,
                "ai.observation_interval_seconds",
                30
            )
        ) + " seconds"
    )}

    {metric_card(
        "Dashboard Refresh",
        str(
            get_path(
                pipeline_config,
                "monitoring.grafana_refresh_seconds",
                5
            )
        ) + " seconds"
    )}

  </div>
</section>

<section class="section">
  <h2 class="section-title">10. Final Conclusion</h2>

  <div class="panel">
    <div class="ai-box">
      {esc(conclusion)}
    </div>
  </div>
</section>

<div class="footer">
  Generated {esc(generated_at)} • AI-Enabled Canary Deployment POC
</div>

</div>
</body>
</html>
"""

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_path.write_text(
        html_output,
        encoding="utf-8"
    )

    return output_path


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate the self-contained AI Canary "
            "deployment HTML report."
        )
    )

    parser.add_argument(
        "--project-root",
        default="",
        help="Project root. Defaults to repository root."
    )

    parser.add_argument(
        "--output",
        default="",
        help="Optional report output path."
    )

    args = parser.parse_args()

    if args.project_root:
        project_root = Path(
            args.project_root
        ).resolve()
    else:
        project_root = (
            Path(__file__)
            .resolve()
            .parent
            .parent
        )

    if args.output:
        output_path = Path(
            args.output
        )

        if not output_path.is_absolute():
            output_path = (
                project_root /
                output_path
            )

        output_path = output_path.resolve()

    else:
        output_path = (
            project_root /
            "runtime" /
            "reports" /
            "ai-canary-deployment-report.html"
        )

    report = build_report(
        project_root,
        output_path
    )

    print("")
    print(
        "============================================================"
    )
    print(
        " AI CANARY DEPLOYMENT REPORT GENERATED"
    )
    print(
        "============================================================"
    )
    print(
        f"Report: {report}"
    )
    print("")
    print(
        "The report contains no external web dependencies."
    )
    print(
        "Telemetry, AI findings and deployment evidence "
        "are embedded directly in the HTML."
    )
    print("")


if __name__ == "__main__":
    main()
