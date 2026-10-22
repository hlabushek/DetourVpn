"""Export/check the current API contract. Run from the repository root."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.config import Settings  # noqa: E402
from vpn_api.main import create_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    app = create_app(Settings(_env_file=None, environment="test"))
    expected = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = ROOT / "packages" / "contracts" / "openapi.json"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            print("OpenAPI is stale: uv run --frozen python scripts/export_openapi.py")
            return 1
        print("OpenAPI contract is current.")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8", newline="\n")
    print("Exported packages/contracts/openapi.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
