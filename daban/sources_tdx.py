# -*- coding: utf-8 -*-
"""通达信协议数据源 —— 长江证券等券商的行情协议同为通达信。

为什么加它: 东财/腾讯之外提供**第三条独立通道**, 券商行情服务器延迟低、境内主站稳定,
且是用户开户券商(长江证券)自己的行情主站。

【实测结论 2026-10-07 —— 已打通】
  服务器清单来源: 用户台式机(100.110.98.121)上真实在用的"金长江财智版客户端"
  C:/zd_cjzq/connect.cfg (HostNum=23), 不是网上流传的老配置(老配置 IP 全已失效)。
  在 AI 服务器(192.168.1.9 境内真实网络) + pytdx 1.72 实测:
  - 21/23 主站 TCP 全通, 沪深创多只个股日K完整取回(最新到 2026-09-30), 三主站结果一致。
  - 个别股(如 601396 中国太保)某日返回 0 条, 属个股无数据, 非通道故障, 会被校验弃用。
  - 之前"全线不可用"的误判根因: (1) 用了失效老 IP; (2) 测试脚本取错字段名
    (pytdx 1.72 返回 datetime + 分离的 year/month/day, 不是 date), 本模块解析已按正确字段。
  - 域名 hq.cjsc.com.cn 在本机 DNS 解析失败, 但境内网络可解析, 一并保留。

依赖与限制:
  - 需 `pip install pytdx`(已在 requirements 中列出)。
  - 通达信服务器为境内主机, **云端(海外IP)通常连不通**, 因此本模块失败只记录不阻塞,
    主链路自动回落到东财/腾讯/同花顺。
  - 通达信返回的是**不复权**价格; 若与主源(前复权)差异过大(除权股), 会被校验拒绝。
"""
import time

from .data import _f, market_of

# 长江证券官方行情主站 —— 来源: 用户客户端 C:\zd_cjzq\connect.cfg (2026-10-07 实测 21/23 可用)
CJSC_SERVERS = [
    ("长江-武汉电信一",   "119.97.142.136", 7709),
    ("长江-武汉电信二",   "61.183.150.145", 7709),
    ("长江-上海电信",     "222.73.171.156", 7709),
    ("长江-成都电信",     "221.236.12.136", 7709),
    ("长江-深圳电信",     "119.147.80.98",  7709),
    ("长江-武汉联通三",   "113.57.31.38",   7709),
    ("长江-天津联通",     "125.39.80.98",   7709),
    ("长江-乌鲁木齐一",   "61.128.111.196", 7709),
    ("长江-乌鲁木齐二",   "61.128.111.197", 7709),
    ("长江-武汉移动",     "111.47.10.68",   7709),
    ("长江-武汉联通",     "220.249.123.195",7709),
    ("长江-武汉电信三",   "61.183.150.136", 7709),
    ("长江-武汉电信四",   "119.97.142.166", 7709),
    ("长江-武汉联通二",   "61.242.180.206", 7709),
    ("长江-武汉电信五",   "116.211.98.141", 7709),
    ("长江-东莞电信",     "183.60.224.26",  7709),
    ("长江-东莞联通",     "120.86.124.116", 7709),
    ("长江-武汉移动二",   "111.48.202.77",  7709),
    ("长江-武汉移动三",   "223.76.255.44",  7709),
    ("长江-武汉电信六",   "58.48.243.38",   7709),
    ("长江-武汉主站",     "hq.cjsc.com.cn", 7709),
]

TDX_SERVERS = [
    # 长江证券(用户券商)真实主站, 2026-10-07 实测 21/23 可用, 按客户端默认优先级排列
    ("119.97.142.136", 7709),   # 武汉电信一
    ("61.183.150.145", 7709),   # 武汉电信二
    ("222.73.171.156", 7709),   # 上海电信
    ("221.236.12.136", 7709),   # 成都电信
    ("119.147.80.98",  7709),   # 深圳电信
    ("113.57.31.38",   7709),   # 武汉联通三
    ("125.39.80.98",   7709),   # 天津联通
    ("61.128.111.196", 7709),   # 乌鲁木齐一
    ("61.128.111.197", 7709),   # 乌鲁木齐二
    ("111.47.10.68",   7709),   # 武汉移动
    ("220.249.123.195",7709),   # 武汉联通
    ("61.183.150.136", 7709),   # 武汉电信三
    ("119.97.142.166", 7709),   # 武汉电信四
    ("61.242.180.206", 7709),   # 武汉联通二
    ("116.211.98.141", 7709),   # 武汉电信五
    ("183.60.224.26",  7709),   # 东莞电信
    ("120.86.124.116", 7709),   # 东莞联通
    ("111.48.202.77",  7709),   # 武汉移动二
    ("223.76.255.44",  7709),   # 武汉移动三
    ("58.48.243.38",   7709),   # 武汉电信六
    ("hq.cjsc.com.cn", 7709),   # 武汉主站(域名, 境内网络可解析)
]

# 通道开关: 2026-10-07 实测长江真实主站可用后, 默认开启。
# 海外云端若连不通, 会自动熔断并回落到东财/腾讯/同花顺, 不影响主链路。
TDX_ENABLED = True

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
            # 不用 heartbeat/auto_retry: 其后台线程为非守护线程, 会导致主进程无法退出
            api = TdxHq_API()
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
