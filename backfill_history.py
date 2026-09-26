# -*- coding: utf-8 -*-
"""回填滚动样本库: 训练宇宙(流动性前600) × 近一年日K → (特征, 次日触板/封板) 样本。
产出 state/samples.jsonl, 作为滚动训练的基础库; 之后每日实盘增量追加。
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import data, features, feedback
from daban.data import board_of

BASE = os.path.dirname(os.path.abspath(__file__))
TRAIN_UNIVERSE = 600
DAYS = 260          # 约一年交易日
MIN_HIST = 31

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def main():
    t0 = time.time()
    log("1) 全市场快照(东财, 失败自动切腾讯枚举源) ...")
    snap = data.fetch_universe()
    log(f"   快照 {len(snap)} 只")
    liquid = [x for x in snap if (x.get("turnover") or 0) >= 2
              and (x.get("price") or 0) >= 3
              and "ST" not in (x.get("name") or "").upper()
              and not (x.get("name") or "").startswith(("N", "C"))]
    liquid.sort(key=lambda x: -(x.get("turnover") or 0))
    sel = liquid[:TRAIN_UNIVERSE]
    codes = [x["code"] for x in sel]
    names = {x["code"]: x.get("name") for x in sel}
    log(f"2) 训练宇宙 {len(codes)} 只, 抓取近一年K线(days={DAYS}) ...")
    kmap = data.fetch_klines_parallel(codes, days=DAYS, workers=12)
    ok = sum(1 for v in kmap.values() if len(v) >= MIN_HIST)
    log(f"   有效K线 {ok}/{len(codes)}")

    log("3) 构造样本(特征日T -> 次日触板/封板) ...")
    n, pos, seal_n = 0, 0, 0
    tmp = os.path.join(feedback.STATE_DIR, "samples.jsonl.tmp")
    os.makedirs(feedback.STATE_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        for code, kl in kmap.items():
            if len(kl) < MIN_HIST + 1: continue
            mult = 1.10 if board_of(code) == "main" else 1.20
            for i in range(29, len(kl) - 1):
                t, nxt = kl[i], kl[i + 1]
                if t.get("pct") is None or nxt.get("high") is None: continue
                f = features.core_features(code, kl[:i + 1], t.get("pct"), t.get("high"), t.get("close"))
                lab = features.label_next(t, nxt, code)
                if lab is None: continue
                f["_date"] = t.get("date"); f["_seal"] = 0
                pc = t.get("close")
                if pc not in (None, 0) and nxt.get("close") is not None:
                    f["_seal"] = 1 if nxt["close"] >= round(pc * mult, 2) - 1e-6 else 0
                fh.write(json.dumps({"code": code, "name": names.get(code),
                                     "label": int(lab), "row": f}, ensure_ascii=False) + "\n")
                n += 1; pos += int(lab); seal_n += int(f["_seal"])
                if n % 20000 == 0:
                    log(f"   ... {n} 条")
    os.replace(tmp, feedback.SAMPLES)
    dates = sorted({json.loads(l)["row"]["_date"] for l in open(feedback.SAMPLES, encoding="utf-8")})
    log(f"4) 样本库完成: {n} 条, 触板 {pos} ({pos/max(1,n):.2%}), 封板 {seal_n}, "
        f"日期跨度 {dates[0]} ~ {dates[-1]} ({len(dates)} 个交易日)")
    log(f"用时 {time.time()-t0:.0f}s")
    log("BACKFILL DONE")

if __name__ == "__main__":
    main()
