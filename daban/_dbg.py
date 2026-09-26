# -*- coding: utf-8 -*-
import requests, time, json, sys
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
beg = time.strftime("%Y%m%d", time.localtime(time.time() - 120 * 2.2 * 86400))
p = {"ut": "7eea3edcaed734bea9cbfc24409ed989",
     "fields1": "f1,f2,f3,f4,f5,f6",
     "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
     "klt": "101", "fqt": "1", "beg": beg, "end": "20500101", "l": "120", "secid": "1.600519"}
r = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params=p,
                 headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=10)
with open(r"C:\Users\Administrator\WorkBuddy\2026-09-22-10-30-21\daban_tool\kdbg.txt", "w", encoding="utf-8") as f:
    f.write("params=%s\n" % p)
    f.write("status=%s\n" % r.status_code)
    f.write(r.text[:1200] + "\n")
    try:
        d = r.json()
        data = d.get("data") or {}
        f.write("parsed name=%s count=%s\n" % (data.get("name"), len(data.get("klines") or [])))
        f.write("tail=%s\n" % (data.get("klines") or [""])[-2:])
    except Exception as e:
        f.write("parse err %s\n" % e)
