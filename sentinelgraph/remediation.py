
import argparse
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "otel-demo"
FLAGS_FILE = DEMO_DIR / "src" / "flagd" / "demo.flagd.json"

# This tool is allowed to change this flag only.
ALLOWED_REMEDIATIONS = {
    "disable_payment_fault": {
        "flag": "paymentUnreachable",
        "target_variant": "off",
    }
}


def prepare_remediation(action):
    if action not in ALLOWED_REMEDIATIONS:
        raise ValueError(f"Action not allowlisted: {action}")

    spec = ALLOWED_REMEDIATIONS[action]

    with FLAGS_FILE.open("r", encoding="utf-8") as f:
        config = json.load(f)

    flag = config.get("flags", {}).get(spec["flag"])
    if not isinstance(flag, dict):
        raise ValueError(f"Flag not found: {spec['flag']}")

    if spec["target_variant"] not in flag.get("variants", {}):
        raise ValueError("Target variant is not defined")

    if flag.get("state") != "ENABLED":
        raise ValueError("Flag is not enabled; refusing remediation")

    return config, spec


def apply_remediation(action):
    config, spec = prepare_remediation(action)
    flag_name = spec["flag"]
    target = spec["target_variant"]

    current = config["flags"][flag_name].get("defaultVariant")
    if current == target:
        print(f"{flag_name} already defaults to {target!r}")
        return

    # Change only the allowlisted flag's default variant.
    config["flags"][flag_name]["defaultVariant"] = target

    # Preserve the original file if writing fails.
    temp_file = FLAGS_FILE.with_suffix(".json.tmp")
    try:
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        temp_file.replace(FLAGS_FILE)
    finally:
        if temp_file.exists():
            temp_file.unlink()

    # Restart only flagd so it reloads the configuration.
    subprocess.run(
        ["docker", "compose", "restart", "flagd"],
        cwd=DEMO_DIR,
        check=True,
        timeout=60,
    )

    print(f"Applied {action}: {flag_name} -> {target}")
    print("Next: verify the live flag and payment telemetry.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=ALLOWED_REMEDIATIONS.keys(),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the remediation; otherwise only validate it.",
    )
    args = parser.parse_args()

    if args.apply:
        apply_remediation(args.action)
    else:
        _, spec = prepare_remediation(args.action)
        print("Validation passed; no changes made.")
        print(f"Allowed flag: {spec['flag']}")
        print(f"Target variant: {spec['target_variant']}")
        print("Use --apply to execute the remediation.")
