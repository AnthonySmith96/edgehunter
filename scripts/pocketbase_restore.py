"""Validate by default; an explicit destination always remains isolated and live disabled."""
import argparse
import json
from pathlib import Path

from edgehunter.ops.pocketbase import PocketBaseRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--restore-isolated", action="store_true")
    parser.add_argument("--credentials-source", type=Path)
    args = parser.parse_args()
    runtime = PocketBaseRuntime(Path(__file__).resolve().parents[1])
    print(json.dumps(runtime.restore(args.backup, args.destination, dry_run=not args.restore_isolated,
                                    credentials_source=args.credentials_source), indent=2))


if __name__ == "__main__":
    main()
