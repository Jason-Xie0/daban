# -*- coding: utf-8 -*-
"""核验指定预测池的次日真实表现(触板/封板), 数据全部来自真实K线。
用法: python verify_pool.py 2026-09-26
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from verify_lastweek import next_day_result  # noqa: E402

POOL_DIR = os.path.join(BASE, "state", "pools")
REPORTS = os.path.join(BASE, "reports")


def main():
    pred_date = sys.argv[1] if len(sys.argv) > 1 else "2026-09-26"
    tag = pred_date.replace("-", "")
    pool = json.load(open(os.path.join(POOL_DIR, f"pool_{tag}.json"), encoding="utf-8"))
    items = pool["items"][:30]
    res = []
    for i, s in enumerate(items, 1):
        r = next_day_result(s["code"], pred_date)
        r.update(rank=i, name=s["name"], prob=s["prob"])
        res.append(r)

    ok = [r for r in res if r.get("ok")]
    next_dates = sorted({r["next_date"] for r in ok})
    nd = next_dates[0] if next_dates else "?"

    def rate(n, key):
        sub = ok[:n]
        if not sub:
            return None
        hit = sum(1 for r in sub if r[key])
        return {"hit": hit, "n": len(sub), "rate": hit / len(sub)}

    lines = [f"# 预测池实测核验: {pred_date} 预测 -> {nd} 真实行情", "",
             f"- 预测池保存于 {pool['date']}, 共 {pool['n_saved']} 只; 核验 Top30(有效 {len(ok)} 只)",
             f"- 口径: 触板=次日最高价触及涨停价(含冲高回落); 封板=次日收盘价封涨停",
             "",
             "| 档位 | 触板率 | 封板率 |", "|---|---|---|"]
    for n in (5, 10, 30):
        t, s = rate(n, "touch"), rate(n, "seal")
        f = lambda x: f"{x['hit']}/{x['n']} = {x['rate']:.0%}" if x else "—"
        lines.append(f"| Top{n} | {f(t)} | {f(s)} |")
    lines += ["", "| 排名 | 代码 | 名称 | 预测概率 | 次日涨跌% | 触板 | 封板 |",
              "|---|---|---|---|---|---|---|"]
    for r in res:
        if not r.get("ok"):
            lines.append(f"| {r['rank']} | {r['code']} | {r['name']} | {r['prob']:.1%} | — | {r.get('why')} | — |")
            continue
        lines.append(f"| {r['rank']} | {r['code']} | {r['name']} | {r['prob']:.1%} | {r['next_pct']:+.2f}% | "
                     f"{'OK' if r['touch'] else '-'} | {'OK' if r['seal'] else '-'} |")
    out = os.path.join(REPORTS, f"池核验_{tag}_{nd}.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("RESULT:", " | ".join(
        f"top{n} touch {(rate(n,'touch') or {}).get('rate', 0):.0%} seal {(rate(n,'seal') or {}).get('rate', 0):.0%}"
        for n in (5, 10, 30)))
    print("MD ->", out)


if __name__ == "__main__":
    main()
