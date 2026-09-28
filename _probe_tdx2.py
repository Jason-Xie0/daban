# -*- coding: utf-8 -*-
"""细查: 哪个服务器连上了, 能否取到数据"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from pytdx.hq import TdxHq_API  # noqa: E402
from daban.sources_tdx import TDX_SERVERS  # noqa: E402

for ip, port in TDX_SERVERS[:3]:
    try:
        api = TdxHq_API(heartbeat=False, auto_retry=False)
        ok = api.connect(ip, port, time_out=5)
        print(f"{ip}:{port} connect={ok}")
        if ok:
            try:
                n = api.get_security_count(1)
                print("   get_security_count(沪) =", n)
            except Exception as e:
                print("   count ERR", e)
            try:
                bars = api.get_security_bars(9, 1, "603278", 0, 5)
                print("   bars =", bars[:2] if bars else bars)
            except Exception as e:
                print("   bars ERR", e)
            api.disconnect()
    except Exception as e:
        print(f"{ip}:{port} ERR {e}")
print("DONE")
