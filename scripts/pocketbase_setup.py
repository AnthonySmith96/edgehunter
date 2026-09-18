"""Install/provision the dedicated local control plane; no secrets on stdout."""
import argparse
import json
from pathlib import Path

from edgehunter.ops.pocketbase import PocketBaseRuntime

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    runtime = PocketBaseRuntime(Path(__file__).resolve().parents[1], data_dir=args.data_dir)
    print(json.dumps(runtime.install(), indent=2))
    print(json.dumps(runtime.provision(), indent=2))
