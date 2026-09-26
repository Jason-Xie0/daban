# -*- coding: utf-8 -*-
"""历史走前验证(walk-forward): 把"1日预测/次日回测"从一年前回放到现在。
每 20 个交易日一个检查点: 仅用当日之前样本训练 → 预测之后每日 Top-K → 对实际触板/封板记分。
严格无未来函数(训练集 _date 严格早于评估日)。
产出: state/track_record.json 追加历史战绩 + 用全库重训最终模型。
"""
import json, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import feedback, model
from daban.features import CORE_FEATURES

BASE = os.path.dirname(os.path.abspath(__file__))
CP_STEP = 20       # 检查点间隔(交易日)
MIN_TRAIN_DAYS = 130
EVAL_TAIL = 22     # 最后一个检查点后至少留的可评估天数

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def main():
    t0 = time.time()
    log("1) 加载滚动样本库 ...")
    rows, y = feedback.load_samples()
    if len(rows) < 20000:
        log(f"样本库不足({len(rows)}), 请先运行 backfill_history.py")
        return
    dates = sorted({r["_date"] for r in rows})
    log(f"   样本 {len(rows)}, 交易日 {len(dates)} ({dates[0]}~{dates[-1]}), 正样本 {sum(y)} ({sum(y)/len(y):.2%})")

    by_date = {}
    for r in rows:
        by_date.setdefault(r["_date"], []).append(r)
    yv_all = np.array(y)

    # 检查点
    cp_idx = list(range(MIN_TRAIN_DAYS, len(dates) - EVAL_TAIL, CP_STEP))
    log(f"2) 检查点 {len(cp_idx)} 个: {[dates[i] for i in cp_idx][:3]} ... {[dates[i] for i in cp_idx][-3:]}")

    # 预排序特征矩阵(一次性构造)
    import pandas as pd
    df = pd.DataFrame(rows)[CORE_FEATURES].apply(pd.to_numeric, errors="coerce")
    X = np.nan_to_num(df.values.astype(float), nan=0.0)
    date_arr = np.array([r["_date"] for r in rows])
    seal_arr = np.array([int(r.get("_seal", 0)) for r in rows])
    y_arr = np.array(y, dtype=int)

    track = {t["pred_date"]: t for t in feedback.load_track()}
    new_entries = 0
    for ci, cp_i in enumerate(cp_idx):
        cp_date = dates[cp_i]
        next_cp = dates[cp_idx[ci + 1]] if ci + 1 < len(cp_idx) else dates[-EVAL_TAIL]
        train_mask = date_arr < cp_date
        clf = model._make_hgb()
        clf.fit(X[train_mask], y_arr[train_mask])
        eval_dates = [d for d in dates[cp_i:] if d < next_cp]
        for d in eval_dates:
            idx = np.where(date_arr == d)[0]
            if len(idx) < 30: continue
            p = clf.predict_proba(X[idx])[:, 1]
            order = np.argsort(-p)
            yy, ss = y_arr[idx], seal_arr[idx]
            def rate(k):
                sel = order[:k]
                n = len(sel)
                return {"touch": round(float(yy[sel].mean()), 4),
                        "seal": round(float(ss[sel].mean()), 4), "n": int(n)}
            entry = {"pred_date": d, "labeled_at": f"walkforward(cp={cp_date})",
                     "source": "walkforward", "n": int(len(idx)),
                     "top5": rate(5), "top10": rate(10), "top30": rate(30)}
            if d not in track or track[d].get("source") == "walkforward":
                track[d] = entry
                new_entries += 1
        log(f"   检查点 {cp_date}: 训练 {int(train_mask.sum())} 样本, 评估 {len(eval_dates)} 日 (累计新战绩 {new_entries})")

    # 写战绩(按日期排序)
    out = sorted(track.values(), key=lambda t: t["pred_date"])
    with open(feedback.TRACK, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log(f"3) 战绩档案已更新: 共 {len(out)} 日 (新增 {new_entries})")

    # 汇总各期 Top10 触板率走势(每10个战绩日抽一句)
    wf = [t for t in out if t.get("source") == "walkforward" and t.get("top10")]
    if wf:
        seg = max(1, len(wf) // 8)
        for i in range(0, len(wf), seg):
            chunk = wf[i:i + seg]
            t10 = np.mean([c["top10"]["touch"] for c in chunk if c["top10"]])
            b10 = np.mean([c["n"] and 0 or 0 for c in chunk]) if False else None
            log(f"   {chunk[0]['pred_date']}~{chunk[-1]['pred_date']}: Top10 触板率均值 {t10:.0%}")

    log("4) 用全库重训最终模型 ...")
    m = model.train(rows, y, save=True)
    bt = model.backtest(rows, y, k_list=(5, 10, 20))
    log(f"   模型: 样本 {m['n_samples']}, 正样本 {m['n_pos']}, AUC {m.get('auc')}, 回测 {bt}")
    flag = os.path.join(feedback.STATE_DIR, "NEED_RETRAIN")
    if os.path.exists(flag):
        os.remove(flag); log("   已清除 NEED_RETRAIN 标记")
    log(f"用时 {time.time()-t0:.0f}s")
    log("WALKFORWARD DONE")

if __name__ == "__main__":
    main()
