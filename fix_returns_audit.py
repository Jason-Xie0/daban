"""收益/标签数据审计与修复。

背景: feedback.py 里 data.fetch_kline(code, 40) 漏传 market 参数, 深市(0 开头)
代码被东财按 1.<code> 解释, 取到上证A股指数等非本标的序列, 导致 2026-09-29 池中
5 只个股的 settle/ret 量级错误(4000~9000), 标签也随之错误。

本脚本:
 1) 用腾讯日K(按代码所属市场正确拼前缀)重算三张已标注池的次日收盘, 与样本库比对;
 2) 凡 settle 偏差 > 5% 的行, 重算 ret / touch / seal / label;
 3) 按修正后的行重算 track_record 里对应池的 top5/10/30 与 ret5/10/30 指标;
 4) 原子写回 samples.jsonl 与 track_record.json。
只读取真实数据, 不做任何估算填充。
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import data                      # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "state", "samples.jsonl")
TRACK = os.path.join(BASE, "state", "track_record.json")
POOLS = os.path.join(BASE, "state", "pools")
DATES = ("2026-09-28", "2026-09-29", "2026-10-08")


def _load_lines():
    with open(SAMPLES, "r", encoding="utf-8") as fh:
        return fh.readlines()


def _klines(codes):
    """并行取腾讯日K(带市场前缀), 返回 {code: {date: row}}。"""
    def one(c):
        mkt = "sh" if data.market_of(c) == 1 else "sz"
        for i in range(3):
            rows = data.fetch_kline_tx(c, 60, mkt)
            if rows:
                return c, {r["date"]: r for r in rows}
            time.sleep(0.4 * (i + 1))
        return c, {}
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, m in ex.map(one, codes):
            out[c] = m
    return out


def main():
    lines = _load_lines()
    idx = {}                                    # 行号 -> sample
    for i, ln in enumerate(lines):
        if '"ret"' not in ln:
            continue
        try:
            s = json.loads(ln)
        except Exception:
            continue
        if s.get("row", {}).get("_date") in DATES:
            idx[i] = s
    print(f"待审样本 {len(idx)} 行")
    codes = sorted({s["code"] for s in idx.values()})
    kl = _klines(codes)
    print(f"取到K线 {sum(1 for v in kl.values() if v)}/{len(codes)} 只")

    # 各池按行号顺序(即落库顺序=池内排名顺序)收集
    per_pool = {d: [] for d in DATES}
    fixed = 0
    missing = 0
    for i in sorted(idx):
        s = idx[i]
        d = s["row"]["_date"]
        m = kl.get(s["code"]) or {}
        nxt = None
        for dt in sorted(m):
            if dt > d:
                nxt = m[dt]
                break
        if nxt is None:
            missing += 1
            per_pool[d].append(s)
            continue
        buy = s.get("buy")
        settle_ref = nxt["close"]
        old = s.get("settle")
        # 只有"以次日收盘结算"(ret_src=close)的行才能用收盘价复核;
        # ret_src=1430 的行是按设计用次日 14:30 快照价结算, 与收盘价本就会不同, 跳过。
        if s.get("ret_src") == "1430":
            per_pool[d].append(s)
            continue
        bad = (not old or not buy) or abs(settle_ref - old) / old > 0.05
        if bad:
            prev = [r for dt, r in sorted(m.items()) if dt <= d]
            pc = prev[-1]["close"] if prev else None
            limit = data.limit_up_price(s["code"], pc) if pc else None
            s["settle"] = settle_ref
            s["ret"] = round(settle_ref / buy - 1, 4) if buy else None
            if limit is not None:
                s["label"] = int(nxt["high"] >= limit)
                s["seal"] = int(nxt["close"] >= limit)
            lines[i] = json.dumps(s, ensure_ascii=False) + "\n"
            fixed += 1
            print(f"  修正 {d} {s['code']} {s['name']}: settle {old} -> {settle_ref} "
                  f"ret={s['ret']} label={s['label']}")
        per_pool[d].append(s)

    print(f"修正 {fixed} 行, 无K线跳过 {missing} 行")
    if not fixed:
        return

    # 重写 samples.jsonl(原子替换)
    tmp = SAMPLES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp, SAMPLES)
    print("samples.jsonl 已更新")

    # 重算 track_record 指标
    with open(TRACK, "r", encoding="utf-8") as fh:
        track = json.load(fh)
    for d in DATES:
        rows = per_pool[d]
        if not rows:
            continue
        rates = [(r["label"], r.get("seal", 0)) for r in rows]
        rets = [r["ret"] for r in rows if r.get("ret") is not None]

        def rate_at(k):
            sub = rates[:k]
            if not sub:
                return None
            return {"touch": round(sum(x[0] for x in sub) / len(sub), 4),
                    "seal": round(sum(x[1] for x in sub) / len(sub), 4), "n": len(sub)}

        def ret_at(k):
            sub = rets[:k]
            if not sub:
                return None
            return {"ret": round(sum(sub) / len(sub), 4),
                    "win": round(sum(1 for x in sub if x > 0) / len(sub), 4), "n": len(sub)}

        for t in track:
            if t["pred_date"] != d:
                continue
            t["n"] = len(rows)
            t["top5"], t["top10"], t["top30"] = rate_at(5), rate_at(10), rate_at(30)
            t["ret5"], t["ret10"], t["ret30"] = ret_at(5), ret_at(10), ret_at(30)
            t["repaired_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {d} 指标已重算: ret10={t['ret10']}")
    with open(TRACK, "w", encoding="utf-8") as fh:
        json.dump(track, fh, ensure_ascii=False, indent=2)
    print("track_record.json 已更新")


if __name__ == "__main__":
    main()
