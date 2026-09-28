# -*- coding: utf-8 -*-
"""通达信协议数据源 —— 长江证券等券商的行情协议同为通达信。

为什么加它: 东财/腾讯之外提供**第三条独立通道**, 券商行情服务器延迟低、盘中数据最全,
且长江证券(用户开户券商)的行情服务器就在此协议下。

如何接自己的券商(长江证券):
  1. 打开长江证券通达信版客户端, 在其安装目录(如 C:\\长江证券\\...)找 config/ 下的
     行情服务器配置(文件名多为 connect.cfg / 服务器列表), 里面有 IP:端口(通常 7709)。
  2. 把该 IP 填到本文件 TDX_SERVERS 列表最前面(优先级最高)。
  3. 本模块会自动按顺序尝试, 第一个能连上的即被使用。

依赖与限制:
  - 需 `pip install pytdx`(已在 requirements 中列出)。
  - 通达信服务器为境内主机, **云端(海外IP)通常连不通**, 因此本模块失败只记录不阻塞,
    主链路自动回落到东财/腾讯。价值主要在本机与 AI 服务器(192.168.1.9)上运行。
  - 通达信返回的是**不复权**价格; 若与主源(前复权)差异过大(除权股), 会被校验拒绝,
    避免污染特征。
"""
import time

from .data import _f, market_of

TDX_SERVERS = [
    # 用户券商(长江证券)服务器优先; 下面是公开通达信行情服务器作为兜底
    ("119.147.212.81", 7709),
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("218.75.126.9", 7709),
    ("114.80.63.12", 7709),
    ("124.71.187.122", 7709),
]
_TIMEOUT = 6
_api = None
_last_err = ""
_failed_all = False      # 本轮进程内整轮连接失败后不再重试, 避免拖慢主链路


def available():
    try:
        import pytdx  # noqa: F401
        return True
    except Exception:
        return False


def _connect():
    """返回已连接的 pytdx api 或 None(带服务器轮询 + 整轮失败熔断)。"""
    global _api, _last_err, _failed_all
    if _api is not None:
        return _api
    if _failed_all:
        return None
    if not available():
        _last_err = "未安装 pytdx"
        _failed_all = True
        return None
    from pytdx.hq import TdxHq_API
    for ip, port in TDX_SERVERS:
        try:
            api = TdxHq_API(heartbeat=True, auto_retry=True)
            if api.connect(ip, port, time_out=_TIMEOUT):
                _api = api
                return _api
        except Exception as e:
            _last_err = f"{ip}:{port} {e}"
    _last_err = _last_err or "全部服务器连接失败"
    _failed_all = True     # 本进程内不再尝试
    return None


def status():
    return {"available": available(), "connected": _api is not None, "last_err": _last_err}


def fetch_kline_tdx(code: str, days=120, ref_close=None):
    """通达信日K(不复权)。ref_close: 主源最后收盘价, 用于除权校验(差异>2%则弃用)。"""
    api = _connect()
    if api is None:
        return []
    try:
        mkt = 1 if market_of(code) == 1 else 0
        bars = []
        start = 0
        while len(bars) < days:
            batch = api.get_security_bars(9, mkt, code, start, 800)   # 9 = 日K
            if not batch:
                break
            bars = list(batch) + bars
            start += 800
            if len(batch) < 800:
                break
        rows = []
        prev = None
        for b in bars[-days:]:
            c = _f(b.get("close"))
            pct = round((c - prev) / prev * 100, 2) if (prev and c) else None
            rows.append({"date": str(b.get("datetime", ""))[:10], "open": _f(b.get("open")),
                         "close": c, "high": _f(b.get("high")), "low": _f(b.get("low")),
                         "vol": _f(b.get("vol")), "amount": _f(b.get("amount")),
                         "amplitude": None, "pct": pct, "turnover": None})
            prev = c
    except Exception as e:
        global _last_err
        _last_err = f"取K线失败 {code}: {e}"
        return []
    if not rows:
        return rows
    if ref_close and rows[-1]["close"]:
        if abs(rows[-1]["close"] - float(ref_close)) / float(ref_close) > 0.02:
            _last_err = f"{code} 复权基准不一致(通达信{rows[-1]['close']} vs 主源{ref_close}), 弃用"
            return []
    return rows
