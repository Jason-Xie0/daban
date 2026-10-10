"""修正 09-28 池 603278 大业股份的 settle —— 并重算 09-28 池指标。

该行 ret_src='1430', 结算价按设计取次日 14:30 快照(11.29), 不是次日收盘(10.56);
restore_0928.py 用收盘价复核时把它误改成 10.56, 这里按原始标注值恢复。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "state", "samples.jsonl")
TRACK = os.path.join(BASE, "state", "track_record.json")
DATE = "2026-09-28"
CODE = "603278"
SETTLE, RET = 11.29, 0.0473

lines = open(SAMPLES, encoding="utf-8").readlines()
hit = 0
for i, ln in enumerate(lines):
    if f'"code": "{CODE}"' not in ln or f'"_date": "{DATE}"' not in ln:
        continue
    s = json.loads(ln)
    if s.get("settle") == SETTLE and s.get("ret") == RET:
        continue
    print(f"还原 {CODE}: settle {s.get('settle')} -> {SETTLE}, ret {s.get('ret')} -> {RET} (ret_src={s.get('ret_src')})")
    s["settle"], s["ret"] = SETTLE, RET
    lines[i] = json.dumps(s, ensure_ascii=False) + "\n"
    hit += 1
assert hit <= 1, "同池同日不应重复"
if hit:
    tmp = SAMPLES + ".tmp"
    open(tmp, "w", encoding="utf-8").writelines(lines)
    os.replace(tmp, SAMPLES)

# 直接从文件重算 09-28 池指标(保持落库顺序=池内排名顺序)
rows = []
for ln in open(SAMPLES, encoding="utf-8"):
    if f'"_date": "{DATE}"' not in ln:
        continue
    s = json.loads(ln)
    if s.get("row", {}).get("_date") == DATE:
        rows.append(s)
print(f"09-28 池样本 {len(rows)} 条")

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


track = json.load(open(TRACK, encoding="utf-8"))
for t in track:
    if t["pred_date"] == DATE:
        t["n"] = len(rows)
        t["top5"], t["top10"], t["top30"] = rate_at(5), rate_at(10), rate_at(30)
        t["ret5"], t["ret10"], t["ret30"] = ret_at(5), ret_at(10), ret_at(30)
        print("09-28 指标:", {k: t[k] for k in ("top5", "top10", "ret5", "ret10", "ret30")})
json.dump(track, open(TRACK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("track_record.json 已更新")
