import os
import random
import threading
import time

from flask import Flask, Response, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


app = Flask(__name__)

APP_VERSION = os.getenv("APP_VERSION", "unknown")
BASE_DELAY_MS = float(os.getenv("DELAY_MS", "65"))
BASE_ERROR_RATE = float(os.getenv("ERROR_RATE", "0.0"))

# Demo control is enabled only for the Canary revision by Jenkins.
# Stable pods can keep this disabled.
DEMO_CONTROL_ENABLED = os.getenv(
    "DEMO_CONTROL_ENABLED",
    "false"
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}


_state_lock = threading.Lock()

_runtime_state = {
    "delay_ms": BASE_DELAY_MS,
    "error_rate": BASE_ERROR_RATE,
}


HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    [
        "endpoint",
        "status",
        "version",
    ],
)


HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    [
        "endpoint",
        "version",
    ],
)


APP_VERSION_INFO = Gauge(
    "app_version_info",
    "Application version information",
    [
        "version",
    ],
)


APP_VERSION_INFO.labels(
    version=APP_VERSION
).set(1)


def snapshot_runtime_state():
    with _state_lock:
        return dict(_runtime_state)


@app.get("/health")
def health():

    HTTP_REQUESTS.labels(
        endpoint="/health",
        status="200",
        version=APP_VERSION,
    ).inc()

    return jsonify(
        {
            "status": "UP",
            "version": APP_VERSION,
        }
    )


@app.get("/api/orders")
def orders():

    state = snapshot_runtime_state()

    delay_ms = max(
        float(state["delay_ms"]),
        0.0
    )

    error_rate = min(
        max(
            float(state["error_rate"]),
            0.0
        ),
        1.0
    )

    start = time.perf_counter()

    try:

        time.sleep(
            delay_ms / 1000.0
        )

        if random.random() < error_rate:

            HTTP_REQUESTS.labels(
                endpoint="/api/orders",
                status="500",
                version=APP_VERSION,
            ).inc()

            return (
                jsonify(
                    {
                        "message":
                            "Order processing failed",

                        "status":
                            "FAILED",

                        "version":
                            APP_VERSION,
                    }
                ),
                500,
            )

        HTTP_REQUESTS.labels(
            endpoint="/api/orders",
            status="200",
            version=APP_VERSION,
        ).inc()

        return jsonify(
            {
                "message":
                    "Order processed successfully",

                "status":
                    "SUCCESS",

                "version":
                    APP_VERSION,
            }
        )

    finally:

        HTTP_REQUEST_DURATION.labels(
            endpoint="/api/orders",
            version=APP_VERSION,
        ).observe(
            time.perf_counter() - start
        )


# ============================================================
# DEMO-ONLY RUNTIME CONTROL
#
# This endpoint is intentionally NOT represented in Grafana or
# in the final deployment report. Jenkins uses it only to create
# controlled degradation on active Canary pods during Scenario 2.
# ============================================================

@app.post("/demo/fault")
def enable_demo_fault():

    if not DEMO_CONTROL_ENABLED:

        return (
            jsonify(
                {
                    "status": "DISABLED",
                    "version": APP_VERSION,
                }
            ),
            403,
        )

    body = request.get_json(
        silent=True
    ) or {}

    delay_ms = body.get(
        "delay_ms",
        request.args.get(
            "delay_ms",
            140
        )
    )

    error_rate = body.get(
        "error_rate",
        request.args.get(
            "error_rate",
            0.08
        )
    )

    try:

        delay_ms = float(
            delay_ms
        )

        error_rate = float(
            error_rate
        )

    except (
        TypeError,
        ValueError
    ):

        return (
            jsonify(
                {
                    "status":
                        "INVALID_PARAMETERS"
                }
            ),
            400,
        )

    if (
        delay_ms < 0
        or
        error_rate < 0
        or
        error_rate > 1
    ):

        return (
            jsonify(
                {
                    "status":
                        "INVALID_PARAMETERS"
                }
            ),
            400,
        )

    with _state_lock:

        _runtime_state[
            "delay_ms"
        ] = delay_ms

        _runtime_state[
            "error_rate"
        ] = error_rate

    return jsonify(
        {
            "status":
                "CONTROL_APPLIED",

            "version":
                APP_VERSION,

            "delay_ms":
                delay_ms,

            "error_rate":
                error_rate,
        }
    )


@app.post("/demo/reset")
def reset_demo_fault():

    if not DEMO_CONTROL_ENABLED:

        return (
            jsonify(
                {
                    "status": "DISABLED",
                    "version": APP_VERSION,
                }
            ),
            403,
        )

    with _state_lock:

        _runtime_state[
            "delay_ms"
        ] = BASE_DELAY_MS

        _runtime_state[
            "error_rate"
        ] = BASE_ERROR_RATE

    return jsonify(
        {
            "status":
                "CONTROL_RESET",

            "version":
                APP_VERSION,

            "delay_ms":
                BASE_DELAY_MS,

            "error_rate":
                BASE_ERROR_RATE,
        }
    )


@app.get("/demo/state")
def demo_state():

    if not DEMO_CONTROL_ENABLED:

        return (
            jsonify(
                {
                    "status": "DISABLED",
                    "version": APP_VERSION,
                }
            ),
            403,
        )

    state = snapshot_runtime_state()

    return jsonify(
        {
            "version":
                APP_VERSION,

            "delay_ms":
                state["delay_ms"],

            "error_rate":
                state["error_rate"],
        }
    )


@app.get("/metrics")
def metrics():

    return Response(
        generate_latest(),
        mimetype=CONTENT_TYPE_LATEST,
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
