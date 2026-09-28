# -*- coding: utf-8 -*-
"""舆情引擎单元测试(合成新闻+固定时间, 不碰真实数据)。"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from daban import sentiment  # noqa: E402

now = time.mktime(time.strptime("2026-09-28 14:30:00", "%Y-%m-%d %H:%M:%S"))
news = [
    {"time": "2026-09-28 14:00:00", "text": "金辰股份公告中标海外大单，签订战略合作协议", "stocks": [("603396", "金辰股份")]},
    {"time": "2026-09-28 10:00:00", "text": "某公司股东拟减持不超过2%股份", "stocks": [("002640", "跨境通")]},
    {"time": "2026-09-27 08:00:00", "text": "该股此前曾获机构调研，基本面稳健", "stocks": [("603278", "大业股份")]},
    {"time": "2026-09-26 08:00:00", "text": "超过36小时的旧新闻不应计入", "stocks": [("603278", "大业股份")]},
    {"time": "2026-09-28 13:00:00", "text": "与候选股无关的市场资讯", "stocks": []},
]
out = sentiment.analyze(news, {"603396": "金辰股份", "002640": "跨境通", "603278": "大业股份"}, now=now)
print("603396:", out["603396"])
print("002640:", out["002640"])
print("603278:", out["603278"])
a = out["603396"]
assert a["net"] == 2 and "中标" in a["kw_pos"] and "合作" in a["kw_pos"], a
b = out["002640"]
assert b["net"] < 0 and "减持" in b["kw_neg"], b
c = out["603278"]
assert c["heat"] == 0.5 and c["net"] == 0, c          # 只有27h前的无情感词新闻, 权重0.5
bf = sentiment.boost_factor(2, 2)
assert bf > 0.5, bf
bf2 = sentiment.boost_factor(-2, 1)
assert bf2 < 0.5, bf2
print(f"boost(净+2,热2)={bf:.2f} > 0.5 > boost(净-2,热1)={bf2:.2f}")
print("PASS 舆情引擎: 时间衰减/关键词匹配/正负净分/加成方向 全部正确")
