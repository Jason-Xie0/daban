# -*- coding: utf-8 -*-
"""从 latest.json 重新渲染 Markdown(应用新的回测口径说明)。"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import report

base = os.path.dirname(os.path.abspath(__file__))
res = json.load(open(os.path.join(base, "reports", "latest.json"), encoding="utf-8"))
md, js = report.save(res, date_tag="final")
print("MD:", md, flush=True)
print("DONE", flush=True)
