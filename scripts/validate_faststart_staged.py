#!/usr/bin/env python3
"""Opt-in readonly validation of the externally staged bitbank bootstrap."""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.faststart_paper import aggregate, load_bootstrap

parser=argparse.ArgumentParser(description="No network; validates staged FAST_START_PROXY input")
parser.add_argument("data_dir", type=Path)
args=parser.parse_args()
bars, risks=load_bootstrap(args.data_dir)
now=datetime.now(timezone.utc)
two, four=aggregate(bars,2,now), aggregate(bars,4,now)
if len(two)<200 or len(four)<300: raise SystemExit("warmup insufficient")
print({"hourly":len(bars),"2h":len(two),"4h":len(four),"zero_volume_risks":len(risks)})
