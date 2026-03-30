#!/usr/bin/env python3
"""Temporary CLI helper for curated Aremko availability checks."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps/mcp-server"))

from src.tools.aremko_availability import check_aremko_availability_data  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consultar disponibilidad curada en aremko.cl")
    parser.add_argument("--service-type", default="tinajas", help="tinajas, cabanas o masajes")
    parser.add_argument("--fecha", default="mañana", help="hoy, mañana o fecha absoluta")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await check_aremko_availability_data(
        service_type=args.service_type,
        fecha=args.fecha,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
