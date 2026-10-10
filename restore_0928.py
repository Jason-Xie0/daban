"""找回 2026-09-28 池的标注样本(79 条)。

背景: 10-09 16:20 的提交 aa4cbab 已把 09-28 池的 79 条样本写入 samples.jsonl,
但随后 16:24 的 `git reset origin/main` 把工作区该文件回滚到云端版本,
79 条样本丢失(09-29 / 10-08 是在 reset 之后才落库的, 所以还在)。

本脚本:
 1) 从 aa4cbab 中取出这 79 行;
 2) 用腾讯日K逐条复核 settle(次日收盘)与 touch/seal(用涨停价推算), 不合格的按真实数据修正;
 3) 追加回 state/samples.jsonl;
 4) 按修正后的样本重算 track_record 中 2026-09-28 的各项指标。
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import data                      # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "state", "samples.jsonl")
TRACK = os.path.join(BASE, "state", "track_record.json")
SRC_SHA = "aa4cbab"
DATE = "2026-09-28"


def main():
    raw = subprocess.run(["git", "show", f"{SRC_SHA}:state/samples.jsonl"],
                         cwd=BASE, capture_output=True, text=True, encoding="utf-8").stdout
    rows = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            s = json.loads(ln)
        except Exception:
            continue
        if s.get("row", {}).get("_date") == DATE:
            rows.append(s)
    print(f"从 {SRC_SHA} 取出 {DATE} 样本 {len(rows)} 条")
    if not rows:
        return 1

    def one(c):
        mkt = "sh" if data.market_of(c) == 1 else "sz"
        for i in range(3):
            kl = data.fetch_kline_tx(c, 60, mkt)
            if kl:
                return c, {r["date"]: r for r in kl}
            time.sleep(0.4 * (i + 1))
        return c, {}
    kmap = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, m in ex.map(one, sorted({r["code"] for r in rows})):
            kmap[c] = m
    print(f"取到K线 {sum(1 for v in kmap.values() if v)}/{len(kmap)} 只")

    fixed, kept, no_kl = 0, 0, 0
    for s in rows:
        m = kmap.get(s["code"]) or {}
        nxt = None
        for dt in sorted(m):
            if dt > DATE:
                nxt = m[dt]
                break
        if nxt is None:
            no_kl += 1
            continue
        buy = s.get("buy")
        old = s.get("settle")
        if old and buy and abs(nxt["close"] - old) / old <= 0.05:
            kept += 1
            continue
        prev = [r for dt, r in sorted(m.items()) if dt <= DATE]
        pc = prev[-1]["close"] if prev else None
        s["settle"] = nxt["close"]
        s["ret"] = round(nxt["close"] / buy - 1, 4) if buy else None
        limit = data.limit_up_price(s["code"], pc) if pc else None
        if limit is not None:
            s["label"] = int(nxt["high"] >= limit)
            s["seal"] = int(nxt["close"] >= limit)
        fixed += 1
        print(f"  修正 {s['code']} {s['name']}: settle {old} -> {nxt['close']} ret={s['ret']} label={s['label']}")
    print(f"复核通过 {kept} 条, 修正 {fixed} 条, 无K线 {no_kl} 条")

    with open(SAMPLES, "a", encoding="utf-8") as fh:
        for s in rows:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"已追加 {len(rows)} 条到 samples.jsonl")

    rates = [(r["label"], r.get("seal", 0)) for r in rows]
    rets = [r["ret"] for r in rows if r.get("ret") is not None]

    def rate_at(k):
        sub = rates[:k]
        return None if not sub else {"touch": round(sum(x[0] for x in sub) / len(sub), 4),
                                     "seal": round(sum(x[1] for x in sub) / len(sub), 4), "n": len(sub)}

    def ret_at(k):
        sub = rets[:k]
        return None if not sub else {"ret": round(sum(sub) / len(sub), 4),
                                     "win": round(sum(1 for x in sub if x > 0) / len(sub), 4), "n": len(sub)}

    with open(TRACK, "r", encoding="utf-8") as fh:
        track = json.load(fh)
    for t in track:
        if t["pred_date"] != DATE:
            continue
        t["n"] = len(rows)
        t["top5"], t["top10"], t["top30"] = rate_at(5), rate_at(10), rate_at(30)
        t["ret5"], t["ret10"], t["ret30"] = ret_at(5), ret_at(10), ret_at(30)
        t["restored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {DATE} 指标已重算: top10={t['top10']} ret10={t['ret10']}")
    with open(TRACK, "w", encoding="utf-8") as fh:
        json.dump(track, fh, ensure_ascii=False, indent=2)
    print("track_record.json 已更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
