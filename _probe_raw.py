# -*- coding: utf-8 -*-
"""直连腾讯 fqkline 原始返回, 检查是否缺 9/25、9/28 的K线"""
import json
import requests

S = requests.Session()
S.trust_env = False
end = "2026-09-28"
start = "2026-08-01"
for code, mkt in [("sz002413", "sz"), ("sh603278", "sh")]:
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={code},day,{start},{end},120,qfq")
    try:
        r = S.get(url, timeout=15)
        js = r.json()
        node = js["data"][code]
        key = "qfqday" if "qfqday" in node else "day"
        arr = node[key]
        print(f"{code}: key={key} n={len(arr)} last5={[a[0] for a in arr[-5:]]}")
    except Exception as e:
        print(f"{code}: ERR {e}")
print("DONE")
