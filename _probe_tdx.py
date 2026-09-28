# -*- coding: utf-8 -*-
"""实测通达信数据源连通性"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from daban import sources_tdx  # noqa: E402

print("pytdx available:", sources_tdx.available())
rows = sources_tdx.fetch_kline_tdx("603278", 20)
print("603278 rows:", len(rows))
if rows:
    print("last3:", [(r["date"], r["close"]) for r in rows[-3:]])
print("status:", sources_tdx.status())
print("DONE")
