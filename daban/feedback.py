# -*- coding: utf-8 -*-
"""滚动反馈闭环: 每日预测落盘 -> 次日实测标注 -> 样本累积 -> 滚动重训 + 战绩档案。

用户定义的节奏: 1日预测/2日回测; 2日预测/3日回测; 依此类推, 逐步完善模型。
- 预测池: state/pools/pool_YYYYMMDD.json (每日 Top200 候选 + 完整特征 + 概率)
- 样本库: state/samples.jsonl  (标注后的 (特征, 次日触板) 样本, 只增)
- 战绩:   state/track_record.json (每日 Top5/10/30 实测触板/封板率)
标注时机: 预测日 P 的目标日 P+1 收盘完成后才可标注(P+1 完整K线存在且非今日)。
"""
import json, os, time
from . import data
from .data import board_of

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE_DIR, "state")
POOL_DIR = os.path.join(STATE_DIR, "pools")
PRICE_DIR = os.path.join(STATE_DIR, "prices")   # 每日预测时刻快照价(T+1 结算价来源)
SAMPLES = os.path.join(STATE_DIR, "samples.jsonl")
TRACK = os.path.join(STATE_DIR, "track_record.json")
MAX_POOL = 200          # 每日保存/标注的候选上限(控制请求量)
POOL_KEEP_DAYS = 400    # 池文件保留天数

def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ---------------- 预测池落盘 ----------------
def save_pool(scored, date_tag=None):
    """scored: score_candidates 的输出(按概率降序)。保存 Top MAX_POOL 供日后标注。"""
    os.makedirs(POOL_DIR, exist_ok=True)
    tag = date_tag or time.strftime("%Y%m%d")
    path = os.path.join(POOL_DIR, f"pool_{tag}.json")
    items = [{"code": s["code"], "name": s["name"], "prob": s["prob"],
              "price": s.get("price"),           # 买入价 = 预测时刻快照价
              "features": {k: v for k, v in s["features"].items()}} for s in scored[:MAX_POOL]]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": time.strftime("%Y-%m-%d"), "n_pool": len(scored),
                   "n_saved": len(items),
                   "note": "可买池: 预测时刻已封涨停(买不进)的标的不在池内",
                   "items": items}, fh, ensure_ascii=False)
    _log(f"  预测池已落盘: {path} ({len(items)} 条, 可买口径)")
    return path

# ---------------- 每日预测时刻价快照(供 T+1 结算) ----------------
def pool_codes():
    """所有未标注预测池涉及的代码集合。"""
    codes = set()
    if not os.path.isdir(POOL_DIR):
        return codes
    for f in os.listdir(POOL_DIR):
        if f.startswith("pool_") and f.endswith(".json"):
            try:
                with open(os.path.join(POOL_DIR, f), "r", encoding="utf-8") as fh:
                    for it in json.load(fh).get("items", []):
                        codes.add(it["code"])
            except Exception:
                continue
    return codes

def save_prices(snap, date_tag=None):
    """保存预测时刻(约14:30)全池代码的快照价, 作为次日结算价来源。只存池内代码, 控制体积。"""
    os.makedirs(PRICE_DIR, exist_ok=True)
    tag = date_tag or time.strftime("%Y%m%d")
    want = pool_codes()
    px = {}
    for row in snap:
        if row["code"] in want and row.get("price"):
            px[row["code"]] = round(float(row["price"]), 3)
    path = os.path.join(PRICE_DIR, f"price_{tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": time.strftime("%Y-%m-%d"), "n": len(px), "px": px},
                  fh, ensure_ascii=False)
    _log(f"  预测时刻价已存: {path} ({len(px)} 只, 用于次日收益结算)")
    return path

def _settle_price(code, target_date):
    """目标日(T+1)预测时刻价 -> (价, '1430'); 无快照 -> (None, None)。"""
    path = os.path.join(PRICE_DIR, f"price_{target_date.replace('-', '')}.json")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            px = json.load(fh).get("px") or {}
        v = px.get(code)
        return (float(v), "1430") if v else (None, None)
    except Exception:
        return None, None

# ---------------- 次日实测标注 ----------------
_TDAYS_CACHE = None

def _trading_days():
    """交易日历(近40个交易日), 用上证指数日K的日期序列。进程内缓存。"""
    global _TDAYS_CACHE
    if _TDAYS_CACHE is None:
        kl = data.fetch_kline("000001", 40, market=1)   # 上证指数
        _TDAYS_CACHE = [r["date"] for r in kl] if kl else []
    return _TDAYS_CACHE

def _next_day_result(code, pred_date):
    """pred_date 次一交易日(且已收盘)的真实表现。用指数交易日历确定"次日";

    个股在该日无K线(停牌) -> 返回 skip=True, 由调用方跳过该股(不拖累整池)。
    """
    tdays = _trading_days()
    after = [d for d in tdays if d > pred_date]
    if not after:
        return {"ready": False, "why": "目标日未完成"}
    next_td = after[0]
    bj = time.gmtime(time.time() + 8 * 3600)        # 北京时间
    today = time.strftime("%Y-%m-%d", bj)
    closed = (bj.tm_hour, bj.tm_min) >= (15, 0)
    if next_td > today or (next_td == today and not closed):
        return {"ready": False, "why": "目标日未完成"}
    kl = []
    for i in range(3):
        kl = data.fetch_kline(code, 40)
        if kl:
            break
        time.sleep(0.6 * (i + 1))
    if not kl:
        return {"ready": False, "why": "无K线", "skip": True}
    prev = [r for r in kl if r["date"] <= pred_date]
    if not prev:
        return {"ready": False, "why": "预测日无交易", "skip": True}
    row = next((r for r in kl if r["date"] == next_td), None)
    if row is None:
        return {"ready": False, "why": f"个股{next_td}无交易(停牌?)", "skip": True}
    prev_close = prev[-1]["close"]
    mult = 1.10 if board_of(code) == "main" else 1.20
    limit = round(prev_close * mult, 2)
    return {"ready": True, "next_date": row["date"], "next_pct": row.get("pct"),
            "next_close": row["close"],
            "touch": row["high"] >= limit - 1e-6, "seal": row["close"] >= limit - 1e-6}

def label_pool(path):
    """标注一个预测池。全部可标注 -> 返回 (samples, metrics, True)；未就绪 -> (None, None, False)。"""
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    pred_date = d["date"]
    items = d["items"]
    samples, rates, rets = [], [], []
    n_1430 = 0
    for i, it in enumerate(items):
        r = _next_day_result(it["code"], pred_date)
        if not r.get("ready"):
            if r.get("skip"):
                # 个股问题(停牌/无数据): 跳过该股, 不让整池顺延
                _log(f"  池 {pred_date} 第{i}条({it['code']})跳过(不影响整池): {r.get('why')}")
                continue
            _log(f"  池 {pred_date} 第{i}条({it['code']})不可标注: {r.get('why')} -> 池整体顺延")
            return None, None, False
        # 收益: 买入价=预测时刻快照价, 结算价=次日预测时刻快照价(缺失则退化为次日收盘)
        buy = it.get("price")
        settle, src = _settle_price(it["code"], r["next_date"])
        if settle is None:
            settle, src = r.get("next_close"), "close"
        if src == "1430":
            n_1430 += 1
        ret = round(settle / buy - 1, 4) if (buy and settle) else None
        row = dict(it["features"])
        row["_date"] = pred_date
        samples.append({"code": it["code"], "name": it["name"], "prob": it["prob"],
                        "label": int(r["touch"]), "seal": int(r["seal"]),
                        "next_date": r["next_date"], "row": row,
                        "buy": buy, "settle": settle, "ret": ret, "ret_src": src})
        rates.append((int(r["touch"]), int(r["seal"])))
        if ret is not None:
            rets.append(ret)
    def rate_at(k):
        sub = rates[:k]
        n = len(sub)
        if not n: return None
        return {"touch": round(sum(x[0] for x in sub) / n, 4),
                "seal": round(sum(x[1] for x in sub) / n, 4), "n": n}
    def ret_at(k):
        sub = [x for x in rets[:k] if x is not None]
        n = len(sub)
        if not n: return None
        return {"ret": round(sum(sub) / n, 4), "win": round(sum(1 for x in sub if x > 0) / n, 4), "n": n}
    if not samples:
        _log(f"  池 {pred_date} 无可用标注(个股全部跳过), 暂不落库")
        return None, None, False
    metrics = {"pred_date": pred_date, "labeled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "n": len(samples), "ret_src_n": n_1430,
               "top5": rate_at(5), "top10": rate_at(10), "top30": rate_at(30),
               "ret5": ret_at(5), "ret10": ret_at(10), "ret30": ret_at(30)}
    return samples, metrics, True

# ---------------- 样本库 ----------------
def append_samples(samples):
    with open(SAMPLES, "a", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")

def append_pairs(rows, y):
    """把 (特征行, 触板标签) 对批量入库(回填用)。"""
    with open(SAMPLES, "a", encoding="utf-8") as fh:
        for row, lab in zip(rows, y):
            fh.write(json.dumps({"label": int(lab), "row": row}, ensure_ascii=False) + "\n")

def load_samples():
    """返回 (rows, y)。样本库不存在返回 ([], [])。"""
    if not os.path.exists(SAMPLES):
        return [], []
    rows, y = [], []
    with open(SAMPLES, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line: continue
            try:
                s = json.loads(line)
            except Exception:
                continue
            rows.append(s["row"]); y.append(int(s["label"]))
    return rows, y

def sample_count():
    rows, _ = load_samples()
    return len(rows)

# ---------------- 战绩档案 ----------------
def load_track():
    if not os.path.exists(TRACK):
        return []
    with open(TRACK, "r", encoding="utf-8") as fh:
        return json.load(fh)

def append_track(metrics):
    track = load_track()
    track = [t for t in track if t["pred_date"] != metrics["pred_date"]]
    track.append(metrics)
    track.sort(key=lambda t: t["pred_date"])
    with open(TRACK, "w", encoding="utf-8") as fh:
        json.dump(track, fh, ensure_ascii=False, indent=2)

def track_summary(n=10):
    track = load_track()[-n:]
    if not track:
        return []
    out = []
    for t in track:
        line = {"date": t["pred_date"], "n": t["n"]}
        for k in ("top5", "top10", "top30"):
            v = t.get(k)
            line[k] = f"{v['touch']:.0%}/{v['seal']:.0%}" if v else "-"
        for k in ("ret5", "ret10", "ret30"):
            v = t.get(k)
            line[k] = f"{v['ret']:+.1%}" if v else "-"
        out.append(line)
    return out

def ret_stats(n=10):
    """近 n 日收益汇总: 各档平均日收益、胜率、等权累计(连乘)。"""
    track = [t for t in load_track() if t.get("ret10") or t.get("ret5")][-n:]
    if not track:
        return None
    out = {"days": len(track), "first": track[0]["pred_date"], "last": track[-1]["pred_date"]}
    for k, key in (("top5", "ret5"), ("top10", "ret10"), ("top30", "ret30")):
        vals = [t[key]["ret"] for t in track if t.get(key)]
        wins = [t[key]["win"] for t in track if t.get(key)]
        if not vals:
            continue
        cum = 1.0
        for v in vals:
            cum *= (1 + v)
        out[k] = {"avg": round(sum(vals) / len(vals), 4),
                  "win": round(sum(wins) / len(wins), 4),
                  "cum": round(cum - 1, 4), "n": len(vals)}
    return out

# ---------------- 主流程: 标注所有到期预测池 ----------------
def process_pending():
    """扫描 pools/, 标注所有目标日已完成的池; 未就绪的顺延。返回本次新标注池数。"""
    if not os.path.isdir(POOL_DIR):
        return 0
    pools = sorted(f for f in os.listdir(POOL_DIR) if f.startswith("pool_") and f.endswith(".json"))
    done = 0
    for f in pools:
        path = os.path.join(POOL_DIR, f)
        try:
            samples, metrics, ready = label_pool(path)
        except Exception as e:
            _log(f"  标注 {f} 异常: {e}")
            continue
        if not ready:
            continue  # 后面的池更新, 无需继续
        append_samples(samples)
        append_track(metrics)
        pos = sum(s["label"] for s in samples)
        _log(f"  ✅ 滚动回测 {metrics['pred_date']}: 样本+{len(samples)}(触板{pos}) | "
             f"Top5 {metrics['top5']['touch']:.0%}/{metrics['top5']['seal']:.0%} "
             f"Top10 {metrics['top10']['touch']:.0%}/{metrics['top10']['seal']:.0%} "
             f"Top30 {metrics['top30']['touch']:.0%}/{metrics['top30']['seal']:.0%}")
        path_done = path[:-5] + ".labeled"
        os.replace(path, path_done)
        done += 1
    return done
