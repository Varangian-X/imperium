#!/usr/bin/env python3
"""CLI utilities for indexing Recall + Obsidian and calculating migration deltas."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import build_index, render_output


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge base index + migration diff tool")
    parser.add_argument("--recall-dir", required=True)
    parser.add_argument("--obsidian-dir", required=True)
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="json")
    parser.add_argument("--output", help="Optional output file path")
    args = parser.parse_args()

    recall = build_index(args.recall_dir, "recall")
    obsidian = build_index(args.obsidian_dir, "obsidian")
    combined = sorted(recall + obsidian, key=lambda x: (x["source"], x["path"]))
    obsidian_fps = {r["fingerprint"] for r in obsidian}
    pending = [r for r in recall if r["fingerprint"] not in obsidian_fps]

    payload = {
        "summary": {
            "recall_count": len(recall),
            "obsidian_count": len(obsidian),
            "pending_migration_count": len(pending),
        },
        "index": render_output(combined, args.format),
        "pending": pending,
    }

    text = json.dumps(payload, indent=2) if args.format == "json" else payload["index"]

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
