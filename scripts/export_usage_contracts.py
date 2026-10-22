"""Export/check internal usage/peer schemas; keep agent routes out of public OpenAPI."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.tunnels.contracts import (  # noqa: E402
    PeerAckInput,
    PeerAckResponse,
    PeerPollInput,
    PeerPollResponse,
)
from vpn_api.tunnels.lifecycle import LifecycleAck, LifecycleCommands, LifecyclePoll  # noqa: E402
from vpn_api.usage.contracts import UsageResult, UsageSampleInput  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (
        ("usage-sample.v1.json", UsageSampleInput),
        ("usage-result.v1.json", UsageResult),
        ("peer-poll.v1.json", PeerPollInput),
        ("peer-commands.v1.json", PeerPollResponse),
        ("peer-ack.v1.json", PeerAckInput),
        ("peer-ack-result.v1.json", PeerAckResponse),
        ("peer-lifecycle-poll.v2.json", LifecyclePoll),
        ("peer-lifecycle-commands.v2.json", LifecycleCommands),
        ("peer-lifecycle-ack.v2.json", LifecycleAck),
    ):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = "urn:project-vpn:" + name
        expected = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = ROOT / "packages/contracts" / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                print("Internal contracts are stale; run scripts/export_usage_contracts.py")
                return 1
        else:
            path.write_text(expected, encoding="utf-8", newline="\n")
    print(
        "Internal usage/peer contracts are current."
        if args.check
        else "Exported internal usage/peer contracts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
