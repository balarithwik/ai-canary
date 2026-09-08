import argparse
import json
from pathlib import Path


# ============================================================
# PROJECT PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

DEFAULT_TEMPLATE = (
    PROJECT_ROOT
    / "kubernetes"
    / "rollout-template.yaml"
)

DEFAULT_RENDER_DIR = (
    PROJECT_ROOT
    / "runtime"
    / "rendered"
)


# ============================================================
# DEFAULT DEMO BEHAVIOUR
#
# Stable = live baseline
# Canary = healthy candidate
#
# Scenario 2 later activates controlled degradation at the
# requested checkpoint without changing image/version/revision.
# ============================================================

DEFAULT_STABLE_DELAY_MS = 60
DEFAULT_STABLE_ERROR_RATE = 0.01

DEFAULT_CANARY_DELAY_MS = 65
DEFAULT_CANARY_ERROR_RATE = 0.00


# ============================================================
# HELPERS
# ============================================================

def load_json(path):

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def validate_percentage(
    value,
    field_name
):

    try:

        value = int(
            value
        )

    except (
        TypeError,
        ValueError
    ):

        raise ValueError(
            f"{field_name} must be an integer."
        )


    if (
        value < 0
        or
        value > 100
    ):

        raise ValueError(
            f"{field_name} must be between 0 and 100."
        )


    return value


def normalize_scenario(
    scenario_data
):

    scenario_name = str(
        scenario_data.get(
            "scenario",
            ""
        )
    ).strip()


    if not scenario_name:

        raise ValueError(
            "Scenario file is missing 'scenario'."
        )


    checkpoints = scenario_data.get(
        "checkpoints",
        []
    )


    if not isinstance(
        checkpoints,
        list
    ):

        raise ValueError(
            "'checkpoints' must be a JSON array."
        )


    normalized = []

    for item in checkpoints:

        normalized.append(
            validate_percentage(
                item,
                "checkpoint"
            )
        )


    if normalized != sorted(
        normalized
    ):

        raise ValueError(
            "Scenario checkpoints must be in ascending order."
        )


    if len(
        set(
            normalized
        )
    ) != len(
        normalized
    ):

        raise ValueError(
            "Scenario checkpoints must not contain duplicates."
        )


    final_weight = (
        validate_percentage(
            scenario_data.get(
                "final_weight",
                100
            ),
            "final_weight"
        )
    )


    if (
        normalized
        and
        final_weight
        <=
        normalized[-1]
    ):

        raise ValueError(
            "final_weight must be greater than the last "
            "analysis checkpoint."
        )


    return {
        "scenario":
            scenario_name,

        "description":
            str(
                scenario_data.get(
                    "description",
                    ""
                )
            ),

        "checkpoints":
            normalized,

        "final_weight":
            final_weight,

        "inject_fault":
            bool(
                scenario_data.get(
                    "inject_fault",
                    False
                )
            ),

        "fault_weight":
            scenario_data.get(
                "fault_weight"
            )
    }


def render_steps(
    mode,
    scenario=None
):

    # Initial Stable deployment has no analysis pause.
    # It should simply settle at 100% and become the live baseline.
    if mode == "stable":

        return (
            "        - setWeight: 100"
        )


    if scenario is None:

        raise ValueError(
            "Canary rendering requires a scenario."
        )


    lines = []


    for checkpoint in scenario[
        "checkpoints"
    ]:

        lines.append(
            f"        - setWeight: {checkpoint}"
        )

        lines.append(
            "        - pause: {}"
        )


    lines.append(
        "        - setWeight: "
        f"{scenario['final_weight']}"
    )


    return "\n".join(
        lines
    )


def replacement_map(
    args,
    steps
):

    if args.mode == "stable":

        delay_ms = (
            args.delay_ms
            if args.delay_ms is not None
            else
            DEFAULT_STABLE_DELAY_MS
        )

        error_rate = (
            args.error_rate
            if args.error_rate is not None
            else
            DEFAULT_STABLE_ERROR_RATE
        )

        demo_control = "false"

    else:

        delay_ms = (
            args.delay_ms
            if args.delay_ms is not None
            else
            DEFAULT_CANARY_DELAY_MS
        )

        error_rate = (
            args.error_rate
            if args.error_rate is not None
            else
            DEFAULT_CANARY_ERROR_RATE
        )

        # Only candidate/Canary pods expose the demo control.
        # Stable pods keep it disabled.
        demo_control = "true"


    return {
        "__ROLLOUT_NAME__":
            args.rollout_name,

        "__NAMESPACE__":
            args.namespace,

        "__APP_NAME__":
            args.app_name,

        "__CONTAINER_NAME__":
            args.container_name,

        "__IMAGE__":
            args.image,

        "__APP_VERSION__":
            args.version,

        "__DELAY_MS__":
            str(
                delay_ms
            ),

        "__ERROR_RATE__":
            str(
                error_rate
            ),

        "__DEMO_CONTROL_ENABLED__":
            demo_control,

        "__CANARY_STEPS__":
            steps,
    }


def render_template(
    template_text,
    replacements
):

    rendered = template_text


    for key, value in replacements.items():

        rendered = rendered.replace(
            key,
            str(
                value
            )
        )


    unresolved = [
        token
        for token in (
            "__ROLLOUT_NAME__",
            "__NAMESPACE__",
            "__APP_NAME__",
            "__CONTAINER_NAME__",
            "__IMAGE__",
            "__APP_VERSION__",
            "__DELAY_MS__",
            "__ERROR_RATE__",
            "__DEMO_CONTROL_ENABLED__",
            "__CANARY_STEPS__",
        )
        if token in rendered
    ]


    if unresolved:

        raise RuntimeError(
            "Unresolved template placeholders: "
            +
            ", ".join(
                unresolved
            )
        )


    return rendered


def default_output_path(
    mode
):

    DEFAULT_RENDER_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    if mode == "stable":

        filename = (
            "stable-rollout.yaml"
        )

    else:

        filename = (
            "canary-rollout.yaml"
        )


    return (
        DEFAULT_RENDER_DIR
        /
        filename
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=
            "Render generic Stable/Canary Argo Rollout manifests."
    )


    parser.add_argument(
        "--mode",
        choices=[
            "stable",
            "canary"
        ],
        required=True
    )


    parser.add_argument(
        "--version",
        required=True,
        help=
            "Generic application version label, e.g. demo-101"
    )


    parser.add_argument(
        "--image",
        required=True,
        help=
            "Docker image/tag to deploy"
    )


    parser.add_argument(
        "--scenario",
        help=
            "Scenario JSON path. Required for --mode canary."
    )


    parser.add_argument(
        "--template",
        default=str(
            DEFAULT_TEMPLATE
        )
    )


    parser.add_argument(
        "--output"
    )


    parser.add_argument(
        "--namespace",
        default="ai-canary"
    )


    parser.add_argument(
        "--rollout-name",
        default="ai-canary-demo"
    )


    parser.add_argument(
        "--app-name",
        default="ai-canary-demo"
    )


    parser.add_argument(
        "--container-name",
        default="ai-canary-demo"
    )


    parser.add_argument(
        "--delay-ms",
        type=float
    )


    parser.add_argument(
        "--error-rate",
        type=float
    )


    args = parser.parse_args()


    scenario = None


    if args.mode == "canary":

        if not args.scenario:

            parser.error(
                "--scenario is required "
                "when --mode canary"
            )


        scenario = normalize_scenario(
            load_json(
                Path(
                    args.scenario
                )
            )
        )


    template_path = Path(
        args.template
    )


    if not template_path.exists():

        raise FileNotFoundError(
            f"Rollout template not found: "
            f"{template_path}"
        )


    template_text = template_path.read_text(
        encoding="utf-8"
    )


    steps = render_steps(
        args.mode,
        scenario
    )


    rendered = render_template(
        template_text,
        replacement_map(
            args,
            steps
        )
    )


    output_path = (
        Path(
            args.output
        )
        if args.output
        else
        default_output_path(
            args.mode
        )
    )


    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    output_path.write_text(
        rendered,
        encoding="utf-8"
    )


    print(
        "\n=========================================="
    )

    print(
        " GENERIC ROLLOUT RENDERER"
    )

    print(
        "=========================================="
    )


    print(
        f"Mode          : "
        f"{args.mode.upper()}"
    )

    print(
        f"Version       : "
        f"{args.version}"
    )

    print(
        f"Image         : "
        f"{args.image}"
    )


    if scenario is not None:

        print(
            f"Scenario      : "
            f"{scenario['scenario']}"
        )

        print(
            "Checkpoints   : "
            +
            " -> ".join(
                str(
                    item
                )
                +
                "%"
                for item in scenario[
                    "checkpoints"
                ]
            )
        )

        print(
            f"Final Weight  : "
            f"{scenario['final_weight']}%"
        )


    print(
        f"Output        : "
        f"{output_path}"
    )


    print(
        "=========================================="
    )


if __name__ == "__main__":

    main()
