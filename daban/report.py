# -*- coding: utf-8 -*-
"""报告生成: 候选代码/名称/预测涨停概率/简要理由, 输出 Markdown + JSON。"""
import json, os, time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(BASE_DIR, "reports")

def _rank(p):
    if p >= 0.6: return "高"
    if p >= 0.45: return "中"
    return "低"

def render_markdown(result):
    lines = []
    lines.append("# A股次日涨停(打板)预测报告")
    lines.append("")
    lines.append(f"- **生成时间**: {result.get('generated_at')}")
    lines.append(f"- **数据时点**: {result.get('data_time')} (盘中)")
    lines.append(f"- **市场状态**: " + result.get("market_summary", ""))
    lines.append(f"- **模型样本**: 训练样本 {result.get('n_samples', '-')} 个, 触板正样本 {result.get('n_pos', '-')} 个, 基线触板率 {result.get('base_rate', 0):.2%}")
    bt = result.get("backtest") or {}
    if bt and "top10" in bt:
        t = bt["top10"]
        lines.append(f"- **回测(后30%按股票样本外)**: Top5 触板率 {bt.get('top5',{}).get('hit_rate',0):.1%} (对比基线 {bt.get('top5',{}).get('base_rate',0):.1%}, 提升 {bt.get('top5',{}).get('lift',0)}x) | "
                     f"Top10 {t.get('hit_rate',0):.1%} (基线 {t.get('base_rate',0):.1%}, 提升 {t.get('lift',0)}x)")
        lines.append(f"- **回测口径说明**: 基线为全市场当日触板率(~4.8%)；Top-K 提升倍数主要源于**活跃股连板延续效应**与**流动性预筛**，不代表\"57%概率涨停\"。触板=次日最高价触及涨停价(含冲高回落)，**不等于收盘封板**，实际打板成功率显著低于此值。")
    tr = result.get("track_record") or []
    if tr:
        lines.append("")
        lines.append("## 滚动实测战绩 (1日预测/次日回测, 真实行情核验)")
        lines.append("")
        lines.append("| 预测日 | 样本数 | Top5 触板/封板 | Top10 触板/封板 | Top30 触板/封板 |")
        lines.append("|---|---|---|---|---|")
        for t in tr:
            lines.append(f"| {t['date']} | {t['n']} | {t.get('top5','-')} | {t.get('top10','-')} | {t.get('top30','-')} |")
    lines.append("")
    lines.append("> ⚠️ 本预测基于真实行情/量能/板块/资讯数据与历史回测，概率为模型估计值，**不构成投资建议**。涨停不可保证，请注意风险。")
    lines.append("")
    lines.append("## 次日涨停候选 Top 榜")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 预测涨停概率 | 评级 | 简要理由 |")
    lines.append("|---|---|---|---|---|---|")
    for i, s in enumerate(result.get("top", []), 1):
        reason = (s.get("reason") or "").replace("|", "/")
        lines.append(f"| {i} | {s['code']} | {s['name']} | **{s['prob']:.1%}** | {_rank(s['prob'])} | {reason} |")
    lines.append("")
    lines.append("## 关键驱动因素说明")
    for s in result.get("top", [])[:10]:
        lines.append(f"- **{s['name']}({s['code']})**: {s.get('detail','')}")
    lines.append("")
    lines.append("## 免责声明")
    lines.append("本报告由量化模型自动生成，概率基于历史统计与实时特征，存在较大不确定性。"
                 "模型严禁虚构，所有数字均来自真实数据源。股市有风险，入市需谨慎，请独立决策。")
    lines.append("")
    lines.append(f"*数据源: 东方财富(行情/K线/板块/指数) + 新浪财经7x24(资讯) | 模型: 逻辑回归+梯度提升 | 生成于 {result.get('generated_at')}*")
    return "\n".join(lines)

def save(result, date_tag=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    tag = date_tag or time.strftime("%Y%m%d_%H%M")
    md_path = os.path.join(REPORT_DIR, f"打板预测_{tag}.md")
    js_path = os.path.join(REPORT_DIR, f"打板预测_{tag}.json")
    md = render_markdown(result)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    # JSON 只存数据, 不重复大文本
    js = {k: v for k, v in result.items()}
    with open(js_path, "w", encoding="utf-8") as fh:
        json.dump(js, fh, ensure_ascii=False, indent=2)
    # 最新软链名
    latest_md = os.path.join(REPORT_DIR, "latest.md")
    latest_js = os.path.join(REPORT_DIR, "latest.json")
    with open(latest_md, "w", encoding="utf-8") as fh: fh.write(md)
    with open(latest_js, "w", encoding="utf-8") as fh: json.dump(js, fh, ensure_ascii=False, indent=2)
    return md_path, js_path
