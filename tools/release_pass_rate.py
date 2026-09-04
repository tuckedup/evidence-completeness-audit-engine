from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", help="JSONL rows: release, passed, total, commit, run_url")
    parser.add_argument("--minimum-releases", type=int, default=30)
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.ledger).read_text(encoding="utf-8").splitlines() if line]
    releases = {row["release"] for row in rows}
    if len(releases) < args.minimum_releases:
        raise SystemExit(f"refusing to claim a cross-release rate: {len(releases)} < {args.minimum_releases}")
    passed = sum(int(row["passed"]) for row in rows)
    total = sum(int(row["total"]) for row in rows)
    report = {
        "schema_version": "1",
        "release_count": len(releases),
        "passed": passed,
        "total": total,
        "pass_rate": passed / total,
        "source_ledger": str(Path(args.ledger).resolve()),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

