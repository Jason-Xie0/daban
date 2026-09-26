# -*- coding: utf-8 -*-
"""回验: 用真实K线核验历史预测的次日触板/封板情况。
覆盖 reports/ 下所有带日期的预测快照, 逐股核验其下一个交易日的真实表现。
"""
import json, os, sys
print("BOOT OK", flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import data
from daban.data import board_of

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
OUT_MD = os.path.join(REPORTS, "上周实测_20260921-0925.md")
OUT_TXT = os.path.join(BASE, "verify_out.txt")

def next_day_result(code, pred_date):
    """返回 dict: traded, prev_close, next_date, next_pct, touch, seal"""
    import time as _t
    kl = []
    for i in range(4):  # 偶发失败重试
        kl = data.fetch_kline(code, 30)
        if kl:
            break
        _t.sleep(0.6 * (i + 1))
    if not kl:
        return {"code": code, "ok": False, "why": "无K线(重试4次失败)"}
    rows = [r for r in kl if r["date"] > pred_date]
    prev = [r for r in kl if r["date"] <= pred_date]
    if not prev:
        return {"code": code, "ok": False, "why": f"预测日({pred_date})无交易(停牌?)"}
    prev_close = prev[-1]["close"]
    if not rows:
        return {"code": code, "ok": False, "why": "预测日之后暂无交易数据"}
    nxt = rows[0]
    mult = 1.10 if board_of(code) == "main" else 1.20
    limit = round(prev_close * mult, 2)
    touch = nxt["high"] >= limit - 0.001
    seal = nxt["close"] >= limit - 0.001
    return {"code": code, "ok": True, "prev_close": prev_close, "limit": limit,
            "next_date": nxt["date"], "next_pct": nxt["pct"], "next_high_pct": None,
            "touch": touch, "seal": seal}

def verify(json_file):
    d = json.load(open(os.path.join(REPORTS, json_file), encoding="utf-8"))
    pred_dt = d.get("data_time", "?")
    pred_date = pred_dt[:10]
    top = d.get("top", [])
    res = []
    for s in top:
        r = next_day_result(s["code"], pred_date)
        r["name"] = s["name"]; r["prob"] = s["prob"]; r["rank"] = s.get("rank")
        res.append(r)
    ok = [r for r in res if r.get("ok")]
    def rate(n, key):
        sub = ok[:n]
        if not sub: return None
        return {"hit": sum(1 for r in sub if r[key]), "n": len(sub),
                "rate": sum(1 for r in sub if r[key]) / len(sub)}
    out = {"file": json_file, "pred_dt": pred_dt, "n_top": len(top),
           "n_ok": len(ok), "skipped": [r for r in res if not r.get("ok")],
           "top5_touch": rate(5, "touch"), "top10_touch": rate(10, "touch"),
           "top30_touch": rate(30, "touch"),
           "top5_seal": rate(5, "seal"), "top10_seal": rate(10, "seal"),
           "top30_seal": rate(30, "seal"), "detail": res}
    return out

def main():
    files = sorted(f for f in os.listdir(REPORTS)
                   if f.startswith("打板预测_") and f.endswith(".json")
                   and "backfill" not in f and "final" not in f)
    results = [verify(f) for f in files]
    lines = ["# 上周(2026-09-21 ~ 09-25)打板预测留存与实测核验", ""]
    lines.append("- 预测工具建成于 09-22(周二)，**09-21(周一) 无预测记录(工具尚不存在)**。")
    lines.append("- reports/ 中仅有 **09-22** 两份快照(12:28 盘中 / 14:26 收市前)。")
    lines.append("- **09-23 ~ 09-25(周三至周五) 未产生预测记录**：定时任务在那几天 14:30 没有留下报告文件"
                 "(定时自动化需 WorkBuddy 会话在线触发)。以下实测核验仅针对有留存的 09-22。")
    lines.append("")
    for r in results:
        lines.append(f"## 预测快照 {r['pred_dt']} (Top{r['n_top']})")
        lines.append("")
        def fmt(x):
            return f"{x['hit']}/{x['n']} = {x['rate']:.0%}" if x else "—"
        lines.append(f"- **次日(09-23)实测触板率**: Top5 {fmt(r['top5_touch'])} | Top10 {fmt(r['top10_touch'])} | Top30 {fmt(r['top30_touch'])}")
        lines.append(f"- **次日(09-23)实测封板率(收盘涨停)**: Top5 {fmt(r['top5_seal'])} | Top10 {fmt(r['top10_seal'])} | Top30 {fmt(r['top30_seal'])}")
        if r["skipped"]:
            lines.append(f"- 剔除: {len(r['skipped'])} 只(停牌/无数据)")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 预测概率 | 次日涨跌% | 次日触板 | 次日封板 |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, x in enumerate(r["detail"], 1):
            if not x.get("ok"):
                lines.append(f"| {i} | {x['code']} | {x['name']} | {x['prob']:.1%} | — | {x.get('why')} | — |")
                continue
            lines.append(f"| {i} | {x['code']} | {x['name']} | {x['prob']:.1%} | {x['next_pct']:+.2f}% | "
                         f"{'✅' if x['touch'] else '❌'} | {'✅' if x['seal'] else '❌'} |")
        lines.append("")
    lines.append("> 核验口径: 触板=次日最高价触及涨停价(含冲高回落)；封板=次日收盘价封死涨停。全部来自东方财富真实K线，无人工干预。")
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    # 控制台摘要
    for r in results:
        print(f"[{r['pred_dt']}] top{r['n_top']} ok={r['n_ok']}")
        for k in ("top5_touch","top10_touch","top30_touch","top5_seal","top10_seal","top30_seal"):
            v = r[k]
            print(f"  {k}: {v['hit']}/{v['n']} = {v['rate']:.0%}" if v else f"  {k}: -")
        print("MD ->", OUT_MD)
    print("DONE")

if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        with open(os.path.join(BASE, "verify_err.txt"), "w", encoding="utf-8") as fh:
            fh.write(traceback.format_exc())
