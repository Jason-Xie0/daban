# -*- coding: utf-8 -*-
"""补算回测并写回模型, 重新生成带回测的报告(复用已有 Top 结果)。"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import data, features, model, report
from daban.run import _fetch_train_rows, _cache_save, TRAIN_UNIVERSE

print("[1] 抓取训练宇宙 K 线 + 构造样本 ...", flush=True)
rows, y = _fetch_train_rows()
print(f"[2] 样本 {len(rows)} 正样本 {sum(y)}", flush=True)
_cache_save(rows, y)
print("[3] 计算回测 ...", flush=True)
m = model.load_model()
bt = model.ensure_backtest(m, rows, y)
print(f"[4] 回测: {bt}", flush=True)

# 重新生成报告(复用已有 latest.json 的 Top, 补上回测)
latest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "latest.json")
res = json.load(open(latest, encoding="utf-8"))
res["backtest"] = bt or {}
res["n_samples"] = m.get("n_samples"); res["n_pos"] = m.get("n_pos")
res["base_rate"] = m.get("base_rate"); res["model_auc"] = m.get("auc")
md, js = report.save(res, date_tag="backfilled")
print(f"[5] 报告已更新: {md}", flush=True)
print("DONE", flush=True)
