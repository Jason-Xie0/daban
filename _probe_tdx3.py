# -*- coding: utf-8 -*-
"""定位: 哪个市场/类别的K线请求能取到数据"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from pytdx.hq import TdxHq_API  # noqa: E402

SRV = [("180.153.18.170", 7709), ("218.75.126.9", 7709), ("124.71.187.122", 7709)]
tests = [(9, 0, "000001"), (9, 1, "600000"), (8, 0, "000001"), (9, 1, "603278")]
for ip, port in SRV:
    api = TdxHq_API(heartbeat=False, auto_retry=False)
    try:
        if not api.connect(ip, port, time_out=5):
            print(f"{ip} connect=False")
            continue
    except Exception as e:
        print(f"{ip} ERR {e}")
        continue
    print(f"== {ip} connected, count(深)={api.get_security_count(0)}")
    for cat, mkt, code in tests:
        try:
            b = api.get_security_bars(cat, mkt, code, 0, 5)
            print(f"   bars(cat={cat},mkt={mkt},{code}) -> {len(b) if b else b}"
                  + (f" last={b[-1].get('datetime')} {b[-1].get('close')}" if b else ""))
        except Exception as e:
            print(f"   bars(cat={cat},mkt={mkt},{code}) ERR {e}")
    try:
        ib = api.get_index_bars(9, 1, "000001", 0, 3)
        print(f"   index_bars -> {len(ib) if ib else ib}")
    except Exception as e:
        print(f"   index_bars ERR {e}")
    api.disconnect()
print("DONE")
