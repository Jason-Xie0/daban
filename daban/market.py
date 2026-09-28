# -*- coding: utf-8 -*-
"""大盘要素: 指数状态 + 市场宽度(上涨/涨停占比), 作为模型特征参与预测。

- 训练侧: 指数取自真实历史K线(上证指数); 宽度用样本宇宙自身的当日涨跌分布做代理
  (样本宇宙约600只活跃股, 与全市场宽度高度相关但不是全市场口径)。
- 服务侧: 指数用实时(或腾讯备用源), 宽度用全市场实时快照计算(真实全市场口径)。
设计要点: 纯客观、可复现, 无未来函数(训练时只用当日及之前的数据)。
"""
from . import data

MARKET_FEATURES = ["idx_pct", "idx_ma5_dev", "idx_mom5", "breadth_up", "zt_share"]


def index_map(days=400):
    """{date: {idx_pct, idx_ma5_dev, idx_mom5}} —— 基于上证指数真实日K。"""
    kl = data.fetch_kline("000001", days, market=1)
    out = {}
    closes = [r.get("close") for r in kl]
    pcts = [r.get("pct") for r in kl]
    for i, r in enumerate(kl):
        if i < 5 or not closes[i]:
            continue
        prev5 = [c for c in closes[i - 5:i] if c]
        ma5 = sum(prev5) / len(prev5) if prev5 else None
        out[r["date"]] = {
            "idx_pct": r.get("pct") if r.get("pct") is not None else 0.0,
            "idx_ma5_dev": round(closes[i] / ma5 - 1, 5) if ma5 else 0.0,
            "idx_mom5": round(sum(x for x in pcts[i - 5:i] if x is not None), 3),
        }
    return out


def daily_breadth(rows, min_n=20):
    """训练样本行(含 _date/day_pct/near_limit) -> {date: {breadth_up, zt_share}} 宽度代理。"""
    agg = {}
    for r in rows:
        d = r.get("_date")
        if not d:
            continue
        a = agg.setdefault(d, [0, 0, 0])
        a[0] += 1
        if (r.get("day_pct") or 0) > 0:
            a[1] += 1
        if (r.get("near_limit") or 0) > 0.999:
            a[2] += 1
    return {d: {"breadth_up": round(v[1] / v[0], 4), "zt_share": round(v[2] / v[0], 4)}
            for d, v in agg.items() if v[0] >= min_n}


def attach(rows, imap=None, bmap=None):
    """给训练行注入大盘要素(缺失填中性值), 原地修改并返回 rows。"""
    imap = imap or {}
    bmap = bmap or {}
    for r in rows:
        d = r.get("_date")
        m = dict(imap.get(d) or {})
        m.update(bmap.get(d) or {})
        for k in MARKET_FEATURES:
            r[k] = float(m.get(k) or 0.0)
    return rows


def today(snap, indices=None):
    """服务侧当日大盘要素。snap=全市场实时快照; indices 可选(缺则内部取)。"""
    out = {k: 0.0 for k in MARKET_FEATURES}
    kl = data.fetch_kline("000001", 30, market=1)
    if kl:
        closes = [r.get("close") for r in kl]
        pcts = [r.get("pct") for r in kl]
        last = kl[-1]
        if len(closes) >= 6 and closes[-1]:
            prev5 = [c for c in closes[-6:-1] if c]
            ma5 = sum(prev5) / len(prev5) if prev5 else None
            # 若指数K线滞后于今日, 用实时指数涨跌修正 dev 的分子
            out["idx_ma5_dev"] = round(closes[-1] / ma5 - 1, 5) if ma5 else 0.0
        out["idx_mom5"] = round(sum(x for x in pcts[-5:] if x is not None), 3)
        out["idx_pct"] = last.get("pct") or 0.0
    if indices:
        for x in indices:
            if x.get("name") and ("上证" in x["name"]) and x.get("pct") is not None:
                out["idx_pct"] = float(x["pct"])
                # 用实时涨跌修正均线偏离(近似): dev = 昨收/MA5 - 1 + 今日pct/100
                if out["idx_ma5_dev"] and last.get("pct") is not None:
                    out["idx_ma5_dev"] = round(out["idx_ma5_dev"] - (last.get("pct") or 0) / 100.0
                                              + float(x["pct"]) / 100.0, 5)
                break
    # 全市场宽度(真实口径)
    n = up = zt = 0
    for s in snap:
        pct = s.get("pct")
        if pct is None:
            continue
        n += 1
        if pct > 0:
            up += 1
        try:
            if data.is_limit_up(pct, s["code"]):
                zt += 1
        except Exception:
            pass
    if n:
        out["breadth_up"] = round(up / n, 4)
        out["zt_share"] = round(zt / n, 4)
    return out
