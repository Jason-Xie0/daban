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
              "features": {k: v for k, v in s["features"].items()}} for s in scored[:MAX_POOL]]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": time.strftime("%Y-%m-%d"), "n_pool": len(scored),
                   "n_saved": len(items), "items": items}, fh, ensure_ascii=False)
    _log(f"  预测池已落盘: {path} ({len(items)} 条)")
    return path

# ---------------- 次日实测标注 ----------------
def _next_day_result(code, pred_date):
    """pred_date 次一交易日(且已收盘)的真实表现。未到/未完成返回 ready=False。"""
    today = time.strftime("%Y-%m-%d")
    kl = []
    for i in range(3):
        kl = data.fetch_kline(code, 30)
        if kl:
            break
        time.sleep(0.6 * (i + 1))
    if not kl:
        return {"ready": False, "why": "无K线"}
    nxt = [r for r in kl if pred_date < r["date"] < today]
    if not nxt:
        return {"ready": False, "why": "目标日未完成"}
    prev = [r for r in kl if r["date"] <= pred_date]
    if not prev:
        return {"ready": False, "why": "预测日无交易"}
    prev_close = prev[-1]["close"]
    r = nxt[0]
    mult = 1.10 if board_of(code) == "main" else 1.20
    limit = round(prev_close * mult, 2)
    return {"ready": True, "next_date": r["date"], "next_pct": r.get("pct"),
            "touch": r["high"] >= limit - 1e-6, "seal": r["close"] >= limit - 1e-6}

def label_pool(path):
    """标注一个预测池。全部可标注 -> 返回 (samples, metrics, True)；未就绪 -> (None, None, False)。"""
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    pred_date = d["date"]
    items = d["items"]
    samples, rates = [], []
    for i, it in enumerate(items):
        r = _next_day_result(it["code"], pred_date)
        if not r.get("ready"):
            _log(f"  池 {pred_date} 第{i}条({it['code']})不可标注: {r.get('why')} -> 池整体顺延")
            return None, None, False
        row = dict(it["features"])
        row["_date"] = pred_date
        samples.append({"code": it["code"], "name": it["name"], "prob": it["prob"],
                        "label": int(r["touch"]), "seal": int(r["seal"]),
                        "next_date": r["next_date"], "row": row})
        rates.append((int(r["touch"]), int(r["seal"])))
    def rate_at(k):
        sub = rates[:k]
        n = len(sub)
        if not n: return None
        return {"touch": round(sum(x[0] for x in sub) / n, 4),
                "seal": round(sum(x[1] for x in sub) / n, 4), "n": n}
    metrics = {"pred_date": pred_date, "labeled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "n": len(samples),
               "top5": rate_at(5), "top10": rate_at(10), "top30": rate_at(30)}
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
        out.append(line)
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
