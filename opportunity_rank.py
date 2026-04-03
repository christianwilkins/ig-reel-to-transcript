#!/usr/bin/env python3
"""Print ranked opportunities from research/opportunities-scorecard.json."""

import json
from pathlib import Path

PATH = Path('/home/chris/.openclaw/workspace/research/opportunities-scorecard.json')


def main() -> int:
    data = json.loads(PATH.read_text())
    items = sorted(data['opportunities'], key=lambda x: x['score'], reverse=True)
    print(f"Updated: {data.get('updated','n/a')}")
    for i, it in enumerate(items, start=1):
        print(f"{i}. {it['name']} | score={it['score']} | status={it['status']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
