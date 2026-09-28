# -*- coding: utf-8 -*-
"""特征工程: 训练(历史日)与服务(实时14:30)使用【同一套特征定义】, 保证同槽同义。

核心特征(仅用价格/量能历史 + 当日涨幅) -> 训练 & 服务通用:
  day_pct, near_limit, vol_ratio_5_20, momentum_5/10/20, zt_10d, zt_20d,
  streak_up, above_ma5/10/20, volatility_10/20
实时增强(服务时才有, 作为有界乘法加成, 不进入核心模型, 避免训练/服务偏斜):
  turnover, volratio, sector_pct, news_n

标签: T+1 触板(次日 high >= 次日涨停价= T.close×板块倍数)。仅用 T 及以前数据。
"""
import math
from .data import board_of, LIMIT_PCT

def _safe_div(a, b):
    if a is None or b in (None, 0): return None
    try:
        v = a / b
        return v if math.isfinite(v) else None
    except Exception:
        return None

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2: return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

def _limit_price(prev_close, code):
    if prev_close in (None, 0): return None
    return prev_close * (1 + LIMIT_PCT[board_of(code)] / 100.0)

def _is_zt(pct, code):
    return pct is not None and pct >= LIMIT_PCT[board_of(code)]

def _sum_pcts(xs):
    xs = [x for x in xs if x is not None]
    return float(sum(xs)) if xs else 0.0

def _up_streak(pcts, cap=12):
    n = 0
    for p in reversed(pcts[-cap:]):
        if p is not None and p > 0: n += 1
        else: break
    return n

def _zt_count(pcts, code, n):
    return sum(1 for p in pcts[-n:] if _is_zt(p, code))

def _near_limit(price, ref_price, code):
    """价格相对当日涨停价的接近度 0~1。ref=当日参考价(收盘价或昨收)。"""
    if price is None or ref_price in (None, 0): return None
    lim = _limit_price(ref_price, code)
    if lim is None: return None
    return max(0.0, min(1.0, 1.0 - (lim - price) / lim))

# ============ 核心特征(训练 & 服务通用) ============
def core_features(code, hist, day_pct, day_high, day_ref):
    """hist: 截至 T 的 K 线(升序, 含 T); day_pct=T当日涨幅; day_high=T当日最高;
    day_ref=当日参考价(训练=T收盘; 服务=昨收)。返回 dict。"""
    pcts = [r.get("pct") for r in hist]
    vols = [r.get("vol") for r in hist]
    closes = [r.get("close") for r in hist]
    f = {"board": board_of(code)}
    f["day_pct"] = day_pct if day_pct is not None else 0.0
    f["near_limit"] = _near_limit(day_high, day_ref, code) or 0.0
    f["vol_ratio_5_20"] = _safe_div(_mean(vols[-5:]), _mean(vols[-20:])) or 1.0
    f["momentum_5"] = _sum_pcts(pcts[-6:-1])   # 前5日(不含当日)
    f["momentum_10"] = _sum_pcts(pcts[-11:-1])
    f["momentum_20"] = _sum_pcts(pcts[-21:-1])
    f["zt_10d"] = _zt_count(pcts[:-1], code, 10)
    f["zt_20d"] = _zt_count(pcts[:-1], code, 20)
    f["streak_up"] = _up_streak(pcts[:-1])
    c = (day_ref or (closes[-1] if closes else None))
    f["above_ma5"] = _safe_div(c, _mean(closes[-6:-1])) - 1 if len(closes) >= 6 else 0.0
    f["above_ma10"] = _safe_div(c, _mean(closes[-11:-1])) - 1 if len(closes) >= 11 else 0.0
    f["above_ma20"] = _safe_div(c, _mean(closes[-21:-1])) - 1 if len(closes) >= 21 else 0.0
    f["volatility_10"] = _std(pcts[-11:-1]) or 0.0
    f["volatility_20"] = _std(pcts[-21:-1]) or 0.0
    return f

def label_next(t_row, next_row, code):
    """T+1 是否触板。"""
    if not t_row or not next_row: return None
    prev_close = t_row.get("close"); high = next_row.get("high")
    if prev_close in (None, 0) or high in (None, 0): return None
    lim = _limit_price(prev_close, code)
    if lim is None: return None
    return 1 if high >= lim - 1e-6 else 0

def build_training_rows(kline_map):
    """kline_map: {code:[row...升序]}。每个 code 在每个 T(需T+1)产一条样本。
    row 附带 _date(特征日) 与 _seal(次日是否收盘封板, 供战绩统计)。"""
    rows, y = [], []
    for code, kl in kline_map.items():
        if len(kl) < 30: continue
        mult = 1.10 if board_of(code) == "main" else 1.20
        for i in range(29, len(kl) - 1):
            t, nxt = kl[i], kl[i + 1]
            if t.get("pct") is None or nxt.get("high") is None: continue
            f = core_features(code, kl[:i + 1], t.get("pct"), t.get("high"), t.get("close"))
            lab = label_next(t, nxt, code)
            if lab is None: continue
            f = dict(f); f["_date"] = t.get("date"); f["_seal"] = 0
            prev_close = t.get("close")
            if prev_close not in (None, 0) and nxt.get("close") is not None:
                f["_seal"] = 1 if nxt["close"] >= round(prev_close * mult, 2) - 1e-6 else 0
            rows.append(f); y.append(lab)
    return rows, y

# ============ 服务时特征(14:30) ============
def serve_features(code, hist_upto_yday, snap):
    """hist_upto_yday: 截至昨日的 K 线(升序); snap: 今日实时快照。
    构造与训练同槽的核心特征: 当日数据用实时值, 历史用截至昨日。"""
    if not hist_upto_yday or len(hist_upto_yday) < 28:
        return None
    day_pct = snap.get("pct")
    day_high = snap.get("price")  # 用现价近似当日高(保守)
    day_ref = snap.get("prev_close")
    f = core_features(code, hist_upto_yday, day_pct, day_high, day_ref)
    # 实时增强(有界加成, 见 predict 层)
    f["snap_turnover"] = snap.get("turnover")
    f["snap_volratio"] = snap.get("volratio")
    f["snap_sector_pct"] = snap.get("_sector_pct")
    f["snap_news_n"] = snap.get("_news_n")
    return f

CORE_FEATURES = [
    "day_pct", "near_limit", "vol_ratio_5_20",
    "momentum_5", "momentum_10", "momentum_20",
    "zt_10d", "zt_20d", "streak_up",
    "above_ma5", "above_ma10", "above_ma20",
    "volatility_10", "volatility_20",
    # 大盘要素(由 daban.market 注入, 缺失时填中性 0)
    "idx_pct", "idx_ma5_dev", "idx_mom5", "breadth_up", "zt_share",
]

if __name__ == "__main__":
    from .data import fetch_kline
    kl = fetch_kline("002564", 60)
    rows, y = build_training_rows({"002564": kl})
    print("samples:", len(rows), "pos:", sum(y))
    print("sample feat:", {k: (round(v,3) if isinstance(v,float) else v) for k,v in rows[-1].items()})
    print("OK")
