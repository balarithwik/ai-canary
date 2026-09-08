import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from discover_rollout import discover_rollout


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

CONFIG_FILE = os.path.join(
    PROJECT_ROOT, "jenkins", "config", "pipeline-config.json"
)
DECISION_FILE = os.path.join(
    SCRIPT_DIR, "ai_decision.json"
)
SCENARIO_STATE_FILE = os.path.join(
    PROJECT_ROOT, "runtime", "scenario-state.json"
)


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def run(command, check=True):
    print("\nExecuting:")
    print(" ".join(str(x) for x in command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.stdout.strip():
        print(result.stdout.strip())

    if result.stderr.strip():
        print(result.stderr.strip())

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with return code {result.returncode}"
        )

    return result


def run_json(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


CONFIG = load_json(CONFIG_FILE)

NAMESPACE = str(CONFIG["project"]["namespace"])
ROLLOUT_NAME = str(CONFIG["project"]["rollout_name"])
CLUSTER_NAME = str(CONFIG["project"]["cluster_name"])
EXPECTED_CONTEXT = f"kind-{CLUSTER_NAME}"


def argo_cli():
    for name in (
        "kubectl-argo-rollouts",
        "kubectl-argo-rollouts.exe",
    ):
        path = shutil.which(name)
        if path:
            return path

    raise RuntimeError(
        "kubectl-argo-rollouts CLI was not found in PATH."
    )


def rollout_resource():
    return run_json([
        "kubectl",
        "get",
        "rollout",
        ROLLOUT_NAME,
        "-n",
        NAMESPACE,
        "-o",
        "json",
    ])


def replicaset_resource(name):
    return run_json([
        "kubectl",
        "get",
        "rs",
        name,
        "-n",
        NAMESPACE,
        "-o",
        "json",
    ])


def current_context():
    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def pause_count(resource):
    return len(
        resource.get("status", {}).get("pauseConditions", []) or []
    )


def current_step(resource):
    value = resource.get("status", {}).get("currentStepIndex")
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def update_state(**updates):
    if os.path.exists(SCENARIO_STATE_FILE):
        try:
            state = load_json(SCENARIO_STATE_FILE)
        except Exception:
            state = {}
    else:
        state = {}

    state.update(updates)
    save_json(SCENARIO_STATE_FILE, state)


def validate_decision_state(decision_data, live):
    expected = decision_data.get("deployment", {})

    if not live.get("canary", {}).get("active", False):
        return False, "No active Canary exists."

    if (
        expected.get("stable_version")
        != live.get("stable", {}).get("version")
    ):
        return False, "Stable version changed after AI analysis."

    if (
        expected.get("canary_version")
        != live.get("canary", {}).get("version")
    ):
        return False, "Canary version changed after AI analysis."

    resource = rollout_resource()

    if pause_count(resource) < 1:
        return False, "Rollout is no longer paused at an AI checkpoint."

    return True, "Live Rollout still matches the AI decision context."


def wait_after_promote(previous_step, timeout):
    elapsed = 0

    while elapsed <= timeout:
        resource = rollout_resource()
        live = discover_rollout()

        phase = str(resource.get("status", {}).get("phase", ""))
        step = current_step(resource)
        pauses = pause_count(resource)

        stable_weight = live.get("stable", {}).get("weight", 0)
        canary_weight = live.get("canary", {}).get("weight", 0)
        canary_active = live.get("canary", {}).get("active", False)

        print(
            f"[INFO] Phase={phase} StepIndex={step} "
            f"PauseConditions={pauses} "
            f"Traffic={stable_weight}%/{canary_weight}% "
            f"Elapsed={elapsed}s"
        )

        if phase == "Degraded":
            raise RuntimeError(
                "Rollout entered Degraded state after promotion."
            )

        if (
            canary_active
            and pauses > 0
            and step > previous_step
        ):
            return "NEXT_CHECKPOINT", live

        if phase == "Healthy" and not canary_active:
            return "COMPLETE", live

        time.sleep(2)
        elapsed += 2

    raise RuntimeError(
        "Timed out waiting for Argo after promotion."
    )


def promote(cli, timeout):
    print("\n==========================================")
    print(" EXECUTING AI-APPROVED PROMOTION")
    print("==========================================")

    before = rollout_resource()
    previous_step = current_step(before)

    run([
        cli,
        "promote",
        ROLLOUT_NAME,
        "-n",
        NAMESPACE,
    ])

    result, live = wait_after_promote(
        previous_step,
        timeout,
    )

    if result == "NEXT_CHECKPOINT":
        checkpoint = int(
            round(
                float(
                    live.get("canary", {}).get("weight", 0)
                )
            )
        )

        update_state(
            current_checkpoint=checkpoint,
            rollout_phase="Paused",
            ai_decision_pending=True,
            decision_pending_execution=False,
            last_executed_action="PROMOTE",
            last_action_status="SUCCESS",
            last_action_at=timestamp(),
        )

        print("\nPROMOTION VERIFICATION")
        print("---------------------------")
        print(f"Next Checkpoint : {checkpoint}%")
        print(
            f"Stable Weight   : "
            f"{live['stable']['weight']}%"
        )
        print(
            f"Canary Weight   : "
            f"{live['canary']['weight']}%"
        )
        print("Status          : PAUSED FOR NEXT AI ANALYSIS")
        return

    final_stable = live.get("stable", {}).get("version")

    update_state(
        current_checkpoint=100,
        rollout_phase="Healthy",
        ai_decision_pending=False,
        decision_pending_execution=False,
        last_executed_action="PROMOTE",
        last_action_status="SUCCESS",
        last_action_at=timestamp(),
        final_outcome="PROMOTED",
        final_stable_version=final_stable,
    )

    print("\nPROMOTION VERIFICATION")
    print("---------------------------")
    print(f"Final Stable    : {final_stable}")
    print("Canary Active   : False")
    print("Status          : ROLLOUT COMPLETE / HEALTHY")


def stable_revision(live):
    stable_rs = live.get("stable", {}).get("replica_set")

    if not stable_rs:
        raise RuntimeError(
            "Stable ReplicaSet could not be discovered."
        )

    rs = replicaset_resource(stable_rs)
    annotations = rs.get("metadata", {}).get("annotations", {})

    revision = (
        annotations.get("rollout.argoproj.io/revision")
        or annotations.get("deployment.kubernetes.io/revision")
    )

    if not revision:
        raise RuntimeError(
            f"Revision annotation missing on Stable ReplicaSet {stable_rs}."
        )

    return str(revision), stable_rs


def wait_after_rollback(expected_stable, timeout):
    elapsed = 0

    while elapsed <= timeout:
        resource = rollout_resource()
        live = discover_rollout()

        phase = str(resource.get("status", {}).get("phase", ""))
        stable = live.get("stable", {}).get("version")
        weight = live.get("stable", {}).get("weight", 0)
        active = live.get("canary", {}).get("active", False)

        print(
            f"[INFO] Phase={phase} Stable={stable} "
            f"StableWeight={weight}% "
            f"CanaryActive={active} Elapsed={elapsed}s"
        )

        if (
            phase == "Healthy"
            and stable == expected_stable
            and not active
        ):
            return live

        time.sleep(3)
        elapsed += 3

    raise RuntimeError(
        "Timed out waiting for previous Stable revision to recover."
    )


def rollback(cli, live, timeout):
    print("\n==========================================")
    print(" EXECUTING AI-APPROVED REVISION ROLLBACK")
    print("==========================================")

    previous_stable = live.get("stable", {}).get("version")
    rejected_canary = live.get("canary", {}).get("version")

    revision, stable_rs = stable_revision(live)

    print(f"\nPrevious Stable : {previous_stable}")
    print(f"Stable RS       : {stable_rs}")
    print(f"Stable Revision : {revision}")
    print(f"Rejected Canary : {rejected_canary}")

    # Restore the full previously known-good Argo Rollout revision.
    # This avoids restoring only the image while leaving Canary env/template
    # changes behind.
    run([
        cli,
        "undo",
        ROLLOUT_NAME,
        "--to-revision",
        revision,
        "-n",
        NAMESPACE,
    ])

    final_live = wait_after_rollback(
        previous_stable,
        timeout,
    )

    update_state(
        rollout_phase="Healthy",
        ai_decision_pending=False,
        decision_pending_execution=False,
        last_executed_action="ROLLBACK",
        last_action_status="SUCCESS",
        last_action_at=timestamp(),
        final_outcome="ROLLED_BACK",
        final_stable_version=previous_stable,
        rejected_canary_version=rejected_canary,
    )

    print("\nROLLBACK VERIFICATION")
    print("---------------------------")
    print(
        f"Stable Version : "
        f"{final_live['stable']['version']}"
    )
    print(
        f"Stable Weight  : "
        f"{final_live['stable']['weight']}%"
    )
    print(
        f"Canary Active  : "
        f"{final_live['canary']['active']}"
    )
    print("Status         : ROLLBACK COMPLETE / HEALTHY")


def pause():
    print("\n==========================================")
    print(" DEPLOYMENT REMAINS PAUSED")
    print("==========================================")

    update_state(
        rollout_phase="Paused",
        ai_decision_pending=True,
        decision_pending_execution=False,
        last_executed_action="PAUSE",
        last_action_status="NO_KUBERNETES_CHANGE",
        last_action_at=timestamp(),
    )

    print("\nNo Kubernetes traffic change executed.")
    print(
        "Run a fresh telemetry/AI cycle before any later action."
    )


def main():
    parser = argparse.ArgumentParser(
        description="AI Canary Deployment Controller"
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the final AI/governance decision.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Seconds to wait for Argo after an action.",
    )

    args = parser.parse_args()

    print("\n==========================================")
    print(" AI DEPLOYMENT CONTROLLER")
    print("==========================================")

    if current_context() != EXPECTED_CONTEXT:
        print("\n[FAIL] Wrong Kubernetes context.")
        print(f"Expected: {EXPECTED_CONTEXT}")
        print(f"Current : {current_context()}")
        return 1

    try:
        decision_data = load_json(DECISION_FILE)
    except Exception as error:
        print(f"\n[FAIL] Unable to load AI decision: {error}")
        return 1

    assessment = decision_data.get("final_assessment", {})

    decision = str(
        assessment.get("decision", "PAUSE")
    ).upper().strip()

    source = str(
        assessment.get("decision_source", "UNKNOWN")
    )

    risk_score = assessment.get("risk_score", 0)
    risk_level = assessment.get("risk_level", "UNKNOWN")

    allowed_decisions = {
        "PROMOTE",
        "PAUSE",
        "ROLLBACK",
    }

    allowed_sources = {
        "AI_DECISION_ENGINE",
        "HARD_SAFETY_GUARDRAIL",
        "AI_UNAVAILABLE_FAILSAFE",
    }

    if decision not in allowed_decisions:
        decision = "PAUSE"

    if source not in allowed_sources:
        print(
            f"\n[FAIL] Unexpected decision source: {source}"
        )
        print("No Kubernetes action executed.")
        return 1

    try:
        live = discover_rollout()
        valid, reason = validate_decision_state(
            decision_data,
            live,
        )
    except Exception as error:
        print(f"\n[FAIL] Live-state validation failed: {error}")
        return 1

    print("\nFINAL DECISION")
    print("---------------------------")
    print(f"Stable          : {live['stable']['version']}")
    print(f"Canary          : {live['canary']['version']}")
    print(
        f"Traffic         : "
        f"{live['stable']['weight']}% Stable / "
        f"{live['canary']['weight']}% Canary"
    )
    print(f"Risk Score      : {risk_score}/100")
    print(f"Risk Level      : {risk_level}")
    print(f"Decision        : {decision}")
    print(f"Decision Source : {source}")

    print("\nDECISION VALIDATION")
    print("---------------------------")
    print(f"Valid  : {valid}")
    print(f"Reason : {reason}")

    if not valid:
        print("\nAI decision will NOT be executed.")
        print("Run fresh telemetry and AI analysis.")
        return 1

    if not args.execute:
        print("\n==========================================")
        print(" READ-ONLY VALIDATION PASSED")
        print("==========================================")
        print(f"\nProposed Action: {decision}")
        print("No Kubernetes action executed.")
        return 0

    try:
        cli = argo_cli()

        print(f"\nArgo CLI    : {cli}")
        print("AUTO ACTION : ENABLED")

        if decision == "PROMOTE":
            promote(cli, args.timeout)
        elif decision == "ROLLBACK":
            rollback(cli, live, args.timeout)
        else:
            pause()

    except Exception as error:
        update_state(
            last_executed_action=decision,
            last_action_status="FAILED",
            last_action_error=str(error),
            last_action_at=timestamp(),
            decision_pending_execution=True,
        )

        print("\n==========================================")
        print(" ACTION FAILED")
        print("==========================================")
        print(f"\nReason: {error}")
        print("No additional deployment action attempted.")
        return 1

    print("\n==========================================")
    print(" AI DEPLOYMENT ACTION COMPLETE")
    print("==========================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
