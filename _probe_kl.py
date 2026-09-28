# -*- coding: utf-8 -*-
"""快速探测: 若干代码的K线最新日期(诊断数据源新鲜度)"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from daban import data  # noqa: E402

for code in ["002413", "002614", "300300", "002238", "301311", "603230"]:
    kl = data.fetch_kline(code, 30)
    if not kl:
        print(code, "-> NO DATA")
        continue
    tail = kl[-3:]
    print(code, "->", [(r["date"], r["close"]) for r in tail])
print("DONE")
