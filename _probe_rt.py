# -*- coding: utf-8 -*-
"""用腾讯实时行情查这些票当前状态(是否停牌/是否有最新成交)"""
import requests

S = requests.Session()
S.trust_env = False
codes = ["sz002413", "sz002614", "sz300300", "sz002238", "sz301311", "sh603230", "sh603278"]
r = S.get("http://qt.gtimg.cn/q=" + ",".join(codes), timeout=15)
r.encoding = "gbk"
for line in r.text.strip().split(";"):
    line = line.strip()
    if not line:
        continue
    body = line.split('="')[-1].rstrip('"')
    p = body.split("~")
    if len(p) < 40:
        print("SHORT:", line[:80])
        continue
    print(f"{p[2]}({p[1] if len(p)>1 else '?'}) price={p[3]} prev={p[4]} pct={p[32]} "
          f"vol={p[6]} turnover={p[38]} time={p[30]}")
print("DONE")
