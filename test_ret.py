# -*- coding: utf-8 -*-
"""收益率口径单元测试(隔离: 临时目录 + 桩数据, 不触碰真实 state)。

验证: 买入价=预测时刻价, 结算价=次日预测时刻价; ret=settle/buy-1;
      快照缺失时退化为次日收盘价; 胜率/均值/Top-K 档位统计正确。
"""
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from daban import feedback  # noqa: E402

tmp = tempfile.mkdtemp(prefix="daban_ret_test_")
feedback.POOL_DIR = os.path.join(tmp, "pools")
feedback.PRICE_DIR = os.path.join(tmp, "prices")
os.makedirs(feedback.POOL_DIR)
os.makedirs(feedback.PRICE_DIR)

# 桩: 5 只票, 次日均"触板"; 买入价 10 元
items = [{"code": f"60000{i}", "name": f"T{i}", "prob": 0.9 - i * 0.01, "price": 10.0,
          "features": {"day_pct": 5.0, "near_limit": 1.0}} for i in range(5)]
pool_path = os.path.join(feedback.POOL_DIR, "pool_20260101.json")
with open(pool_path, "w", encoding="utf-8") as fh:
    json.dump({"date": "2026-01-01", "n_pool": 5, "n_saved": 5, "items": items}, fh)

# 次日预测时刻价: 11.0 / 10.5 / 9.5 / 10.0 / 9.0  → 收益 +10%/+5%/-5%/0%/-10%
settle = {"600000": 11.0, "600001": 10.5, "600002": 9.5, "600003": 10.0, "600004": 9.0}
with open(os.path.join(feedback.PRICE_DIR, "price_20260102.json"), "w", encoding="utf-8") as fh:
    json.dump({"date": "2026-01-02", "n": 5, "px": settle}, fh)

# 桩: 跳过真实K线请求
feedback._next_day_result = lambda code, pred_date: {
    "ready": True, "next_date": "2026-01-02", "next_pct": 10.0, "next_close": 10.8,
    "touch": True, "seal": True}

samples, metrics, ready = feedback.label_pool(pool_path)
assert ready, "应可标注"
rets = [s["ret"] for s in samples]
print("rets:", rets, "src:", samples[0]["ret_src"])
assert rets == [0.1, 0.05, -0.05, 0.0, -0.1], f"收益率算错: {rets}"
assert all(s["ret_src"] == "1430" for s in samples), "应使用次日预测时刻价"
r5 = metrics["ret5"]
assert r5["n"] == 5 and abs(r5["ret"] - 0.0) < 1e-9 and r5["win"] == 0.4, f"Top5 统计错: {r5}"
print("metrics ret5:", r5, "| touch5:", metrics["top5"])
print("PASS 收益率口径正确(14:30买入/次日14:30结算, 均值/胜率/档位统计)")
