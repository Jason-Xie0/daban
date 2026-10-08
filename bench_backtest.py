# -*- coding: utf-8 -*-
"""回测批处理基准: 分解测量 加载样本 -> 特征矩阵 -> 多折 HGB 训练 的真实耗时。
用途: 在服务器 / NAS 上跑同一份代码同一份数据, 对比算力与迁移价值。
"""
import json, os, sys, time, platform
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


log(f"机器: {platform.node()} | {platform.processor() or 'n/a'}")
log(f"Python {platform.python_version()} | 逻辑核 {os.cpu_count()}")

t0 = time.time()
import pandas as pd

from daban import feedback, model
from daban.features import CORE_FEATURES

log(f"[A][导入库] {time.time()-t0:.2f}s")

t1 = time.time()
rows, y = feedback.load_samples()
log(f"[B][加载样本] {len(rows)} 条 / {time.time()-t1:.2f}s")

t2 = time.time()
# 用样本中实际存在的数值特征列, 兼容任意数据版本
feat_cols = [k for k, v in rows[0].items()
             if isinstance(v, (int, float)) and not k.startswith("_")]
df = pd.DataFrame(rows)[feat_cols].apply(pd.to_numeric, errors="coerce")
X = np.nan_to_num(df.values.astype(float), nan=0.0)
y_arr = np.array(y, dtype=int)
log(f"[C][特征矩阵] {X.shape} / {time.time()-t2:.2f}s")
log(f"    实际可用特征 {len(feat_cols)} 个 (代码 CORE_FEATURES 要求 {len(CORE_FEATURES)} 个)")

dates = sorted({r["_date"] for r in rows})
log(f"    交易日 {len(dates)} ({dates[0]} ~ {dates[-1]}), 正样本率 {y_arr.mean():.2%}")

K = 6
t3 = time.time()
for i in range(K):
    n = int(len(y_arr) * (0.55 + i * 0.075))
    clf = model._make_hgb()
    ts = time.time()
    clf.fit(X[:n], y_arr[:n])
    log(f"[D]折{i+1} 训练 n={n} / {time.time()-ts:.2f}s")
train_total = time.time() - t3
log(f"[D][训练合计] {K} 折 / {train_total:.2f}s  (均值 {train_total/K:.2f}s)")

log(f"=== TOTAL {time.time()-t0:.2f}s ===")
