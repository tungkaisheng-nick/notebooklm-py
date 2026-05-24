#!/usr/bin/env python3
"""
Combined runner: run the audit first, then generate next month's content calendar.

Usage:
    python run_all.py \
        --username YOUR_IG_USERNAME \
        --niche "your niche" \
        --audience "your target audience"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Full Instagram audit + content calendar")
    parser.add_argument("--username", "-u", required=True)
    parser.add_argument("--password", "-p", default=None)
    parser.add_argument("--posts", type=int, default=30)
    parser.add_argument("--niche", "-n", required=True)
    parser.add_argument("--audience", "-a", required=True)
    parser.add_argument("--freq", type=int, default=12)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    print("\n=== Step 1: Instagram Audit ===\n")
    audit_cmd = [
        sys.executable, "audit.py",
        "--username", args.username,
        "--posts", str(args.posts),
    ]
    if args.password:
        audit_cmd += ["--password", args.password]
    subprocess.run(audit_cmd, check=True)

    print("\n=== Step 2: Content Calendar ===\n")
    cal_cmd = [
        sys.executable, "content_generator.py",
        "--niche", args.niche,
        "--audience", args.audience,
        "--freq", str(args.freq),
    ]
    if args.api_key:
        cal_cmd += ["--api-key", args.api_key]
    subprocess.run(cal_cmd, check=True)

    print("\n=== Done ===")
    print("Files saved:")
    for f in ["audit_report.json", "content_calendar.json"]:
        if Path(f).exists():
            print(f"  {f}")


if __name__ == "__main__":
    main()
