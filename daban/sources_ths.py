# -*- coding: utf-8 -*-
"""同花顺日K数据源 —— 独立于东财/腾讯的第三条通道(HTTP, 境内)。

为什么加它: 东财被限流、腾讯偶发超时的极端情况下, 有第三条独立链路兜底;
数据来自同花顺行情库, 与东财/腾讯並非同一上游。

接口: http://d.10jqka.com.cn/v6/line/hs_<code>/01/last.js
  -> quotebridge_v6_line_hs_000001_01_last({..., "data": "日期,开,高,低,收,量,额,...;..."})
  一次返回最近约 140 个交易日(够用: 我们只需 ~120 根)。

注意:
  - 返回为**不复权**价, 与主源(前复权)在除权股上会有差异; 因此只在东财+腾讯都失败时才启用,
    并且会做合理性校验(末根日期不能过旧、价格必须为正)。
  - 服务为 http 明文 + 境内域名, 云端(海外 IP)可能不通, 失败一律静默返回 []。
"""
import json
import re
import time
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 8


def available():
    return True


def fetch_kline_ths(code: str, days: int = 120):
    """同花顺日K(不复权)。失败返回 []。"""
    url = f"http://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "http://stockpage.10jqka.com.cn/",
    })
    # 显式禁用代理: 本机环境变量里的代理会拦这类请求
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        raw = opener.open(req, timeout=_TIMEOUT).read().decode("utf-8", "ignore")
    except Exception:
        return []
    m = re.search(r"\((\{.*\})\)", raw, re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(1))
    except Exception:
        return []
    if str(d.get("code", "0")) not in ("0", "None") and d.get("data") is None:
        return []
    rows = []
    prev = None
    for line in (d.get("data") or "").split(";"):
        p = line.split(",")
        if len(p) < 6:
            continue
        try:
            dt = f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:8]}"
            o, h, l, c = float(p[1]), float(p[2]), float(p[3]), float(p[4])
            vol = float(p[5]) if p[5] else None
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        pct = round((c - prev) / prev * 100, 2) if prev else None
        rows.append({"date": dt, "open": o, "close": c, "high": h, "low": l,
                     "vol": vol, "amount": None, "amplitude": None,
                     "pct": pct, "turnover": None})
        prev = c
    if not rows:
        return []
    # 合理性校验: 最后一根不能比今天早太多(避免拿到陈旧数据)
    if rows[-1]["date"] < time.strftime("%Y-%m-%d",
                                        time.localtime(time.time() - 12 * 86400)):
        return []
    return rows[-days:]
