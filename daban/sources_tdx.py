# -*- coding: utf-8 -*-
"""通达信协议数据源 —— 长江证券等券商的行情协议同为通达信。

为什么加它: 东财/腾讯之外提供**第三条独立通道**, 券商行情服务器延迟低。

【实测结论 2026-10-07, 详见 TDX_SERVERS 注释】
  - 长江证券官方行情主站(取自通达信 hq_hosts 券商配置清单)全部已下线/不可达:
    域名 telhq.cjsc.com.cn / tdxhq.cjsc.com 已 NXDOMAIN, 各 IP 端口 7709 TCP 超时。
  - 目前在线的通达信云行情服务器(华为云/腾讯云双线主站)可 TCP 连通、能取到
    "证券数量/除权/财务", 但**拒绝返回 K 线与实时报价**(返回 0 条)。
    已在两台真实网络主机(本机 + AI 服务器 192.168.1.9)用 pytdx 1.72 与 mootdx 0.11
    双库交叉验证, 结论一致 → 该通道当前对本工具不可用, 保留代码以便日后恢复。
  - 因此第三数据源改由 `sources_ths.py`(同花顺日K) 承担, 本模块作为可选兜底。

如何接自己的券商(长江证券)的正确做法:
  装上"金长江财智版客户端"后, 在其安装目录找 config 下的行情服务器配置
  (connect.cfg / 服务器列表), 把 IP:端口(通常 7709)填到 TDX_SERVERS 最前面。

依赖与限制:
  - 需 `pip install pytdx`(已在 requirements 中列出)。
  - 通达信服务器为境内主机, **云端(海外IP)通常连不通**, 因此本模块失败只记录不阻塞,
    主链路自动回落到东财/腾讯/同花顺。
  - 通达信返回的是**不复权**价格; 若与主源(前复权)差异过大(除权股), 会被校验拒绝。
"""
import time

from .data import _f, market_of

# 长江证券官方行情主站(来源: 通达信 hq_hosts 券商服务器配置清单, 供日后券商侧恢复时使用)
CJSC_SERVERS = [
    ("长江-武汉电信主站1", "59.173.7.36", 7709),
    ("长江-武汉电信主站2", "202.103.27.6", 7709),
    ("长江-武汉电信主站3", "59.173.13.66", 7709),
    ("长江-长沙电信主站", "202.103.67.28", 7709),
    ("长江-北京网通主站", "211.154.46.132", 7709),
    ("长江-武汉网通主站", "220.249.119.66", 7709),
    ("长江-哈尔滨网通", "218.9.148.196", 7709),
    ("长江-郑州网通", "219.156.123.36", 7709),
]

TDX_SERVERS = [
    # 长江证券(用户券商)官方主站优先 —— 2026-10 实测均不可达, 保留待券商侧恢复
    ("59.173.7.36", 7709),        # 长江-武汉电信主站1
    ("202.103.27.6", 7709),       # 长江-武汉电信主站2
    ("59.173.13.66", 7709),       # 长江-武汉电信主站3
    ("202.103.67.28", 7709),      # 长江-长沙电信主站
    ("211.154.46.132", 7709),     # 长江-北京网通主站
    ("220.249.119.66", 7709),     # 长江-武汉网通主站
    ("218.9.148.196", 7709),      # 长江-哈尔滨网通
    ("219.156.123.36", 7709),     # 长江-郑州网通
    # 公开通达信云行情服务器(2026-10 实测: 能连但拒答K线/报价), 作为兜底
    ("119.97.185.59", 7709),
    ("124.71.187.122", 7709),
    ("116.205.183.150", 7709),
    ("180.153.18.170", 7709),
    ("60.191.117.167", 7709),
]

# 通道开关: 2026-10 实测该协议对本工具全线不可用(见文件头说明),
# 默认关闭以免每轮多花约 48 秒在连接超时上; 券商侧恢复或换到可用服务器后置 True 即可。
TDX_ENABLED = False

_TIMEOUT = 6
_api = None
_last_err = ""
_failed_all = False      # 本轮进程内整轮连接失败后不再重试, 避免拖慢主链路


def available():
    if not TDX_ENABLED:
        return False
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
        _last_err = "通达信通道未启用(2026-10 实测不可用, 见文件头)" if not TDX_ENABLED else "未安装 pytdx"
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
