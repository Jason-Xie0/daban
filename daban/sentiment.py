# -*- coding: utf-8 -*-
"""舆情打分: 基于真实新闻文本的关键词情感分析(确定性、可解释、无 LLM 依赖)。

设计原则:
- 只用真实存在的新闻文本(新浪7x24), 每个得分都能列出匹配到的关键词, 可人工复核;
- 时间衰减: 12 小时内权重 1.0, 12~36 小时 0.5(越新越重要), 超过 36 小时不计;
- 参与预测的方式: 与换手率/量比同层的**服务时有界加成**(logit 空间), 不进训练矩阵 ——
  历史舆情无法回补, 进矩阵会造成训练/服务特征不一致(会作弊)。
"""
import time
from collections import defaultdict

POS_KW = [
    "中标", "中选", "签约", "预增", "预盈", "扭亏", "业绩大增", "回购", "增持",
    "重组", "获批", "订单", "合作", "涨价", "提价", "分红", "收购", "量产",
    "投产", "突破", "新高", "涨停", "上涨", "利好", "股权激励", "战略协议",
]
NEG_KW = [
    "减持", "质押", "立案", "调查", "处罚", "罚款", "亏损", "预亏", "诉讼",
    "退市", "解禁", "问询", "警示", "冻结", "终止", "下滑", "下跌", "跌停",
    "利空", "违规", "商誉减值", "监管函", "风险提示", "停牌核查",
]


def _age_hours(tstr, now_ts):
    """资讯时间 -> 距今小时数; 解析失败返回 None。"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            t = time.mktime(time.strptime(tstr, fmt))
            return max(0.0, (now_ts - t) / 3600.0)
        except Exception:
            continue
    return None


def analyze(news_items, codes_names, now=None, max_age_hours=36):
    """按股票聚合舆情。

    news_items: data.fetch_news() 输出 [{time, text, stocks:[(code,name)]}]
    codes_names: {code: name} 候选集(只算这些股票)
    返回 {code: {"pos","neg","net","heat","kw_pos","kw_neg","last"}}
    """
    now_ts = now if now is not None else time.time()
    want = {c: (n or "") for c, n in (codes_names or {}).items()}
    agg = {c: {"pos": 0.0, "neg": 0.0, "heat": 0.0, "kw_pos": [], "kw_neg": [], "last": ""}
           for c in want}
    for it in news_items:
        text = it.get("text") or ""
        tstr = it.get("time") or ""
        age = _age_hours(tstr, now_ts)
        if age is None or age > max_age_hours:
            continue
        w = 1.0 if age <= 12 else 0.5
        hits = {}                                     # code -> 是否相关
        for c, _n in it.get("stocks") or []:
            if c in want:
                hits[c] = True
        if want:
            for c, nm in want.items():
                if nm and len(nm) >= 3 and nm in text:
                    hits[c] = True
        if not hits:
            continue
        kw_pos = [k for k in POS_KW if k in text]
        kw_neg = [k for k in NEG_KW if k in text]
        for c in hits:
            a = agg[c]
            a["heat"] += w
            a["pos"] += w * len(kw_pos)
            a["neg"] += w * len(kw_neg)
            for k in kw_pos:
                if k not in a["kw_pos"]:
                    a["kw_pos"].append(k)
            for k in kw_neg:
                if k not in a["kw_neg"]:
                    a["kw_neg"].append(k)
            if tstr > a["last"]:
                a["last"] = tstr
    out = {}
    for c, a in agg.items():
        pos, neg = round(a["pos"], 2), round(a["neg"], 2)
        out[c] = {"pos": pos, "neg": neg, "net": round(pos - neg, 2),
                  "heat": round(a["heat"], 2), "kw_pos": a["kw_pos"][:5],
                  "kw_neg": a["kw_neg"][:5], "last": a["last"]}
    return out


def boost_factor(net, heat):
    """net(净情感) + heat(热度) -> [0,1] 加成因子。无舆情时 0.5(中性)。"""
    def clip01(x):
        return max(0.0, min(1.0, float(x)))
    if not heat:
        return 0.5
    sn = clip01((net + 3.0) / 6.0)      # net [-3,3] -> [0,1]
    hn = clip01(heat / 3.0)             # 热度越高, 情感权重越可信
    return clip01(0.7 * sn + 0.3 * hn)
