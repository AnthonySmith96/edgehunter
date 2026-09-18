"""Local PocketBase snapshot. Stop its dedicated service before invoking this script."""
import argparse
import json
from pathlib import Path

from edgehunter.ops.pocketbase import PocketBaseRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = PocketBaseRuntime(Path(__file__).resolve().parents[1], data_dir=args.data_dir)
    print(json.dumps(runtime.backup(args.destination), indent=2))


if __name__ == "__main__":
    main()
