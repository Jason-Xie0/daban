# -*- coding: utf-8 -*-
"""测试网易财经K线接口(第三条数据源, 与东财/腾讯相互独立)"""
import requests

S = requests.Session()
S.trust_env = False
# 网易历史行情CSV: code 前缀 0=沪 1=深
for code, pre in [("603278", "0"), ("002413", "0"), ("300300", "1")]:
    mkt = "1" if code.startswith(("0", "3")) else "0"
    url = ("http://quotes.money.163.com/service/chddata.html"
           f"?code={mkt}{code}&start=20260901&end=20260928&fields=TCLOSE;HIGH;LOW;TOPEN;LCLOSE;PCHG;TURNOVER;VOTURNOVER")
    try:
        r = S.get(url, timeout=12)
        r.encoding = "gbk"
        lines = [l for l in r.text.strip().splitlines() if l.strip()]
        print(f"{code}: rows={len(lines)-1} head={lines[1][:70] if len(lines)>1 else 'NA'}")
        print(f"        last={lines[-1][:70]}")
    except Exception as e:
        print(f"{code}: ERR {e}")
print("DONE")
