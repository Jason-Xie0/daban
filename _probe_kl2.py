# -*- coding: utf-8 -*-
"""对比东财与腾讯K线的新鲜度"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from daban import data  # noqa: E402

for code in ["002413", "002614", "300300"]:
    em = data._fetch_kline_em(code, 30)
    tx = data.fetch_kline_tx(code, 30)
    e = em[-1]["date"] if em else "NONE"
    t = tx[-1]["date"] if tx else "NONE"
    print(f"{code}: EM_last={e} ({len(em)} rows) | TX_last={t} ({len(tx)} rows)")
print("DONE")
