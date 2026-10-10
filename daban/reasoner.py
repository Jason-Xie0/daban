# -*- coding: utf-8 -*-
"""理由生成: 调用本地大模型, 仅基于给定的真实特征与资讯撰写"简要理由"。
- LLM 不可用/失败时, 用特征模板兜底, 保证报告稳定产出
- 严禁虚构: prompt 明确"只能使用提供的数字与资讯, 不得编造"
"""
import json, os
import requests

LLM_BASE = os.environ.get("DABAN_LLM_BASE", "http://192.168.1.55:8081/v1")
LLM_MODEL = os.environ.get("DABAN_LLM_MODEL", "")  # 空=自动取 /models 第一个
LLM_TIMEOUT = int(os.environ.get("DABAN_LLM_TIMEOUT", "180"))

def _pick_model(base):
    try:
        r = requests.get(base + "/models", timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        ms = r.json().get("data") or r.json().get("models") or []
        if ms:
            return ms[0].get("id") or ms[0].get("name")
    except Exception:
        pass
    return None

def _chat(base, model, prompt, max_tokens=2500):
    r = requests.post(base + "/chat/completions",
                      json={"model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.3, "max_tokens": max_tokens},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=LLM_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    return d["choices"][0]["message"]["content"].strip()

def llm_available(base=LLM_BASE):
    try:
        r = requests.get(base + "/models", timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code == 200
    except Exception:
        return False

# ---------------- 特征->人话 ----------------
def _fmt_pct(v):
    return None if v is None else f"{v:+.1f}%"

def _fmt_num(v, nd=1):
    return None if v is None else f"{v:.{nd}f}"

def describe(stock):
    """把一只候选的真实特征转成一段客观事实描述(供 LLM 与兜底共同使用)。"""
    f = stock.get("features", {})
    bits = []
    b = f.get("board", "main")
    board_name = {"main": "主板", "cy": "创业板", "kc": "科创板", "bj": "北交所"}.get(b, "主板")
    bits.append(f"{stock.get('name')}({stock.get('code')}) 属{board_name}")
    if f.get("snap_pct") is not None:
        bits.append(f"今日盘中涨幅 {_fmt_pct(f.get('snap_pct'))}")
    if f.get("snap_near_limit") is not None:
        bits.append(f"现价距涨停价约 {_fmt_num((1 - f['snap_near_limit']) * 100, 1)}%")
    if f.get("snap_turnover") is not None:
        bits.append(f"换手率 {_fmt_pct(f.get('snap_turnover'))}")
    if f.get("snap_volratio") is not None:
        bits.append(f"量比 {_fmt_num(f.get('snap_volratio'))}")
    if f.get("streak_up"):
        bits.append(f"近 {_fmt_num(f.get('streak_up'), 0)} 日连续收涨")
    if f.get("zt_10d"):
        bits.append(f"近10日曾涨停 {_fmt_num(f.get('zt_10d'), 0)} 次")
    if f.get("consec_limit_up_5d"):
        bits.append(f"近5日连续涨停 {_fmt_num(f.get('consec_limit_up_5d'), 0)} 日(连板)")
    if f.get("vol_ratio_5_20") is not None:
        bits.append(f"5日均量/20日均量 {_fmt_num(f.get('vol_ratio_5_20'))}")
    if f.get("above_ma5") is not None:
        bits.append(f"现价较5日线 {_fmt_pct(f.get('above_ma5') * 100)}")
    if f.get("sector_pct") is not None:
        bits.append(f"所属行业今日 {_fmt_pct(f.get('sector_pct'))}")
    nh = stock.get("news", {}).get("n", 0)
    if nh:
        bits.append(f"近36小时相关新闻 {nh} 条")
    return "，".join(bits) + "。"

def template_reason(stock):
    """LLM 不可用时的客观兜底理由(仅复述真实特征, 不含主观夸大)。"""
    prob = stock.get("prob")
    d = describe(stock)
    return f"模型综合动量/量能/位置/板块热度打分。事实: {d} 预测次日触板概率 {prob:.1%}。"

def reason_with_llm(stocks, base=LLM_BASE):
    """为 stocks(list, 已含 name/code/prob/features/news) 批量生成理由。
    返回 {code: reason}。失败返回空 dict(调用方用兜底)。"""
    if not stocks: return {}
    out = {}
    if not llm_available(base):
        return out
    model = _pick_model(base) or LLM_MODEL
    if not model:
        return out
    # 分批, 每批 5 只, 降低单次 token
    for i in range(0, len(stocks), 5):
        chunk = stocks[i:i + 5]
        lines = []
        for s in chunk:
            lines.append(f"- {s['code']} {s['name']} | 概率 {s['prob']:.1%} | {describe(s)}")
            if s.get("news", {}).get("samples"):
                lines.append("  相关: " + " / ".join(s["news"]["samples"]))
        prompt = (
            "你是A股短线(打板)分析师。下面是若干候选股的【客观事实数据】(今日盘中/量能/位置/板块/新闻)。\n"
            "请为每只股票写一条【不超过45字的简要理由】, 说明它次日可能涨停的驱动因素。\n"
            "严格要求: 只能使用上面提供的数字与资讯; 不得编造任何数据、业绩或消息; 不得夸大; "
            "若事实不足以支撑, 就如实写'驱动因素偏弱'。\n"
            "输出格式: 每行一条, '代码: 理由'。\n\n候选:\n" + "\n".join(lines)
        )
        try:
            text = _chat(base, model, prompt, max_tokens=3000)
            for line in text.splitlines():
                line = line.strip()
                if ":" in line or "：" in line:
                    sep = ":" if ":" in line else "："
                    code = line.split(sep, 1)[0].strip()
                    reason = line.split(sep, 1)[1].strip()
                    code = "".join(ch for ch in code if ch.isdigit())
                    if len(code) == 6 and reason:
                        out[code] = reason[:80]
        except Exception:
            continue
    return out

if __name__ == "__main__":
    print("llm available:", llm_available())
    demo = {"name": "天汽科技", "code": "002564", "prob": 0.62,
            "features": {"board": "main", "snap_pct": 10.1, "snap_turnover": 12.3,
                         "snap_volratio": 3.2, "streak_up": 3, "zt_10d": 2,
                         "vol_ratio_5_20": 2.1, "sector_pct": 5.4},
            "news": {"n": 2, "samples": ["公司中标重大项目"]}}
    print("desc:", describe(demo))
    r = reason_with_llm([demo])
    print("llm_reason:", r)
    print("template:", template_reason(demo))
