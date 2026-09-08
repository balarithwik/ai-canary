import json
import subprocess


NAMESPACE = "ai-canary"
ROLLOUT_NAME = "ai-canary-demo"


def run_json(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )
    return json.loads(result.stdout)


def get_image(replica_set):
    containers = (
        replica_set
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )

    if not containers:
        return "UNKNOWN"

    return containers[0].get("image", "UNKNOWN")


def get_app_version(replica_set):
    containers = (
        replica_set
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )

    if containers:
        env_list = containers[0].get("env", [])

        for env in env_list:
            if env.get("name") == "APP_VERSION":
                return env.get("value", "UNKNOWN")

    image = get_image(replica_set)

    if ":" in image:
        return image.rsplit(":", 1)[1]

    return image


def get_rs_hash(replica_set):
    labels = replica_set.get(
        "metadata", {}
    ).get(
        "labels", {}
    )

    return (
        labels.get("rollouts-pod-template-hash")
        or labels.get("pod-template-hash")
        or ""
    )


def find_replica_set(replica_sets, identifier):
    if not identifier:
        return None

    for rs in replica_sets:
        name = rs.get("metadata", {}).get("name", "")
        rs_hash = get_rs_hash(rs)

        if (
            identifier == name
            or identifier == rs_hash
            or name.endswith("-" + identifier)
        ):
            return rs

    return None


def get_weight(status, name):
    try:
        return int(
            status["canary"]["weights"][name]["weight"]
        )
    except (
        KeyError,
        TypeError,
        ValueError
    ):
        return None


def discover_rollout():
    rollout = run_json([
        "kubectl",
        "get",
        "rollout",
        ROLLOUT_NAME,
        "-n",
        NAMESPACE,
        "-o",
        "json"
    ])

    rs_data = run_json([
        "kubectl",
        "get",
        "rs",
        "-n",
        NAMESPACE,
        "-l",
        "app=ai-canary-demo",
        "-o",
        "json"
    ])

    replica_sets = rs_data.get("items", [])

    status = rollout.get("status", {})

    stable_identifier = status.get(
        "stableRS",
        ""
    )

    current_identifier = status.get(
        "currentPodHash",
        ""
    )

    stable_rs = find_replica_set(
        replica_sets,
        stable_identifier
    )

    current_rs = find_replica_set(
        replica_sets,
        current_identifier
    )

    active_canary = (
        stable_identifier
        and current_identifier
        and stable_identifier != current_identifier
    )

    stable_weight = get_weight(
        status,
        "stable"
    )

    canary_weight = get_weight(
        status,
        "canary"
    )

    if not active_canary:
        stable_weight = 100
        canary_weight = 0

    elif stable_weight is None or canary_weight is None:

        stable_ready = 0
        canary_ready = 0

        if stable_rs:
            stable_ready = (
                stable_rs
                .get("status", {})
                .get("readyReplicas", 0)
            )

        if current_rs:
            canary_ready = (
                current_rs
                .get("status", {})
                .get("readyReplicas", 0)
            )

        total = stable_ready + canary_ready

        if total > 0:
            stable_weight = round(
                stable_ready / total * 100
            )

            canary_weight = round(
                canary_ready / total * 100
            )

    result = {
        "rollout": ROLLOUT_NAME,
        "namespace": NAMESPACE,
        "phase": status.get(
            "phase",
            "UNKNOWN"
        ),
        "step_index": status.get(
            "currentStepIndex",
            None
        ),
        "stable": {
            "replica_set": (
                stable_rs
                .get("metadata", {})
                .get("name", "UNKNOWN")
                if stable_rs
                else "UNKNOWN"
            ),
            "version": (
                get_app_version(stable_rs)
                if stable_rs
                else "UNKNOWN"
            ),
            "image": (
                get_image(stable_rs)
                if stable_rs
                else "UNKNOWN"
            ),
            "weight": stable_weight
        },
        "canary": {
            "active": bool(active_canary),
            "replica_set": (
                current_rs
                .get("metadata", {})
                .get("name", "NONE")
                if active_canary and current_rs
                else "NONE"
            ),
            "version": (
                get_app_version(current_rs)
                if active_canary and current_rs
                else "NONE"
            ),
            "image": (
                get_image(current_rs)
                if active_canary and current_rs
                else "NONE"
            ),
            "weight": canary_weight
        }
    }

    return result


def main():
    print("\n==========================================")
    print(" GENERIC ARGO ROLLOUT DISCOVERY")
    print("==========================================")

    try:
        info = discover_rollout()

    except subprocess.CalledProcessError as error:
        print("\nUnable to query Kubernetes.")
        print(error.stderr)
        return

    print("\nROLLOUT")
    print("---------------------------")
    print(f"Name          : {info['rollout']}")
    print(f"Namespace     : {info['namespace']}")
    print(f"Phase         : {info['phase']}")
    print(f"Current Step  : {info['step_index']}")

    print("\nSTABLE")
    print("---------------------------")
    print(
        f"ReplicaSet    : "
        f"{info['stable']['replica_set']}"
    )
    print(
        f"Version       : "
        f"{info['stable']['version']}"
    )
    print(
        f"Image         : "
        f"{info['stable']['image']}"
    )
    print(
        f"Traffic Weight: "
        f"{info['stable']['weight']}%"
    )

    print("\nCANARY")
    print("---------------------------")
    print(
        f"Active        : "
        f"{info['canary']['active']}"
    )
    print(
        f"ReplicaSet    : "
        f"{info['canary']['replica_set']}"
    )
    print(
        f"Version       : "
        f"{info['canary']['version']}"
    )
    print(
        f"Image         : "
        f"{info['canary']['image']}"
    )
    print(
        f"Traffic Weight: "
        f"{info['canary']['weight']}%"
    )

    print("\n==========================================")
    print(" DISCOVERY COMPLETE")
    print("==========================================")

    print("\nJSON OUTPUT")
    print("---------------------------")
    print(
        json.dumps(
            info,
            indent=2
        )
    )


if __name__ == "__main__":
    main()