# -*- coding: utf-8 -*-
"""模型层: 真实历史触板标签训练 + 校准 + 回测。严禁虚构。
- 核心特征 CORE_FEATURES 训练/服务同槽
- 分类器: 逻辑回归(L1) 与 梯度提升 概率均值
- 服务时对实时信号(换手/量比/板块/资讯)做有界 logit 加成, 不污染核心模型
- 回测: 前70%训练 / 后30%按日滚动 Top-K 触板命中率 vs 基线
"""
import base64, json, os, pickle, time
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier

from .features import CORE_FEATURES

POS_WEIGHT = 30
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "state", "daban_model.json")

def _make_hgb():
    return HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05,
                                          max_depth=4, l2_regularization=1.0, random_state=42)

def to_matrix(rows):
    df = pd.DataFrame(rows)[CORE_FEATURES]
    return df.apply(pd.to_numeric, errors="coerce")

def train(rows, y, save=True):
    X = to_matrix(rows)
    yv = np.array(y, dtype=int)
    n = len(yv); pos = int(yv.sum())
    Xf = np.nan_to_num(X.values.astype(float), nan=0.0)
    lr = LogisticRegression(max_iter=3000, C=0.3, penalty="l1", solver="liblinear",
                            class_weight={0: 1, 1: POS_WEIGHT})
    hgb = _make_hgb()
    lr.fit(Xf, yv); hgb.fit(Xf, yv)
    model = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_samples": int(n), "n_pos": pos,
        "base_rate": round(pos / n, 6) if n else 0,
        "features": CORE_FEATURES, "pos_weight": POS_WEIGHT,
        "lr_coef": lr.coef_.tolist(), "lr_inter": float(lr.intercept_[0]),
        "hgb": base64.b64encode(pickle.dumps(hgb)).decode(),
        "auc": _safe_auc(Xf, yv, lr),
    }
    if save:
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, "w", encoding="utf-8") as fh:
            json.dump(model, fh, ensure_ascii=False)
    return model

def _safe_auc(Xf, yv, lr):
    try:
        from sklearn.metrics import roc_auc_score
        return round(float(roc_auc_score(yv, lr.predict_proba(Xf)[:, 1])), 4)
    except Exception:
        return None

def load_model():
    if not os.path.exists(MODEL_PATH): return None
    with open(MODEL_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)

def ensure_backtest(m, rows, y):
    """若模型缺回测结果, 用训练样本补算并写回。"""
    if m.get("backtest"):
        return m["backtest"]
    bt = backtest(rows, y, k_list=(5, 10, 20))
    if "error" not in bt:
        m["backtest"] = bt
        with open(MODEL_PATH, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False)
    return m.get("backtest")

def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))

def _sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))

def _serve_boost(rows):
    """实时信号 -> logit 空间有界加成。每个因子映射到 [-0.15, +0.55]。"""
    def clip01(x):
        return None if x is None else max(0.0, min(1.0, float(x)))
    z = np.zeros(len(rows))
    for i, r in enumerate(rows):
        tr = clip01((r.get("snap_turnover") or 0) / 25)            # 换手 0~25% -> 0~1
        vr = clip01((r.get("snap_volratio") or 0) / 5)             # 量比 0~5 -> 0~1
        sp = (r.get("snap_sector_pct") or 0)
        sp = max(0.0, min(1.0, (sp + 2) / 8))                       # 板块 -2%~+6% -> 0~1
        nn = clip01((r.get("snap_news_n") or 0) / 5)               # 新闻 0~5 -> 0~1
        boost = 0.30 * (tr or 0) + 0.20 * (vr or 0) + 0.35 * sp + 0.15 * nn
        z[i] = boost * 1.0 - 0.15  # 映射到约 [-0.15, +0.85]
    return np.clip(z, -0.15, 0.85)

def predict_proba(model, rows):
    X = to_matrix(rows)
    Xf = np.nan_to_num(X.values.astype(float), nan=0.0)
    lr_coef = np.array(model["lr_coef"][0]); lr_inter = model["lr_inter"]
    p1 = _sigmoid(Xf @ lr_coef + lr_inter)
    hgb = pickle.loads(base64.b64decode(model["hgb"].encode()))
    p2 = hgb.predict_proba(Xf)[:, 1]
    p = 0.5 * (p1 + p2)
    # 实时加成(有界)
    if any(r.get("snap_turnover") is not None or r.get("snap_volratio") is not None
           for r in rows):
        z = _logit(p) + _serve_boost(rows)
        p = _sigmoid(z)
    return np.clip(p, 0.02, 0.97).tolist()

def backtest(rows, y, k_list=(5, 10, 20)):
    """rows 须含 '_date' 且按日期升序。前70%训练, 后30%按日滚动。"""
    n = len(y)
    if n < 300: return {"error": f"样本不足({n})"}
    Xf = np.nan_to_num(to_matrix(rows).values.astype(float), nan=0.0)
    yv = np.array(y, dtype=int)
    split = int(n * 0.7)
    clf = _make_hgb()
    clf.fit(Xf[:split], yv[:split])
    from collections import OrderedDict
    groups = OrderedDict()
    for i, r in enumerate(rows):
        groups.setdefault(r.get("_date", "x"), []).append(i)
    reports = []
    for date, idxs in groups.items():
        idx = np.array(idxs)
        if len(idx) < 30: continue
        p = clf.predict_proba(Xf[idx])[:, 1]
        yy = yv[idx]
        order = np.argsort(-p)
        for K in k_list:
            sel = order[:K]
            reports.append({"K": K, "hit_rate": float(yy[sel].mean()),
                            "base_rate": float(yy.mean()), "n": int(len(yy))})
    out = {}
    for K in k_list:
        rr = [x for x in reports if x["K"] == K]
        if not rr: continue
        hr = float(np.mean([x["hit_rate"] for x in rr]))
        br = float(np.mean([x["base_rate"] for x in rr]))
        out[f"top{K}"] = {"hit_rate": round(hr, 4), "base_rate": round(br, 4),
                          "lift": round(hr / br, 2) if br else 0.0, "days": len(rr)}
    return out

if __name__ == "__main__":
    print("model module OK ->", MODEL_PATH)
