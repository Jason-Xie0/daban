# -*- coding: utf-8 -*-
"""数据层: 东方财富(行情/K线/板块/指数) + 新浪7x24(资讯)。所有接口均已实测可用。"""
import requests, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
S = requests.Session()
S.headers.update({"User-Agent": UA})
S.trust_env = False  # 忽略系统代理, 防止代理故障导致全部请求失败

UNIVERSE_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"   # 沪深主板+创业板(不含北交所, 打板主力池)

# ---------------- 板块/涨停规则 ----------------
def board_of(code: str) -> str:
    if code.startswith(("688", "689")): return "kc"
    if code.startswith(("300", "301", "302")): return "cy"
    if code.startswith(("8", "4", "92")): return "bj"
    return "main"

LIMIT_PCT = {"main": 9.8, "cy": 19.8, "kc": 19.8, "bj": 29.8}

def is_limit_up(pct, code) -> bool:
    try: pct = float(pct)
    except (TypeError, ValueError): return False
    return pct >= LIMIT_PCT[board_of(code)]

# 各板块涨停幅度(由昨收推算涨停价)
LIMIT_MULT = {"main": 1.10, "cy": 1.20, "kc": 1.20, "bj": 1.30}

def limit_up_price(code, prev_close):
    """由昨收推算涨停价(与交易所一致: 标准四舍五入到分)。
    注: ST 股为 5% 限制, 但候选池已排除 ST。
    不使用快照里的 limit_up 字段——东财源该字段(f15)实为当日最高价, 语义不一致。"""
    if not prev_close: return None
    try:
        from decimal import Decimal, ROUND_HALF_UP
        d = (Decimal(str(prev_close)) * Decimal(str(LIMIT_MULT[board_of(code)]))
             ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(d)
    except (TypeError, ValueError, ArithmeticError):
        return None

def is_sealed(x) -> bool:
    """预测时刻该股是否已封涨停 —— 封板则挂单买不进, 不应纳入推荐。
    主判据: 现价 >= 由昨收精确推算的涨停价;
    缺昨收/现价时退化为涨幅阈值判断。"""
    code = x.get("code")
    price, lu = x.get("price"), limit_up_price(code, x.get("prev_close"))
    if lu and price:
        try:
            return float(price) >= lu - 0.001
        except (TypeError, ValueError):
            pass
    return is_limit_up(x.get("pct"), code)

# ---------------- HTTP ----------------
_EM_BLOCKED_UNTIL = 0.0   # 东财熔断: 连续失败后一段时间内快速失败, 不再等待重试

def _get(url, params=None, headers=None, timeout=12, retries=4):
    global _EM_BLOCKED_UNTIL
    is_em = "eastmoney.com" in url
    if is_em and time.time() < _EM_BLOCKED_UNTIL:
        raise ConnectionError("eastmoney 熔断中(近期连续失败), 直接走备用源")
    last = None
    for i in range(retries + 1):
        try:
            r = S.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                _EM_BLOCKED_UNTIL = 0.0
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
            if is_em and r.status_code in (403, 429):
                _EM_BLOCKED_UNTIL = time.time() + 600
        except Exception as e:
            last = e
            if is_em and isinstance(e, ConnectionError):
                _EM_BLOCKED_UNTIL = time.time() + 600   # 连接被断: 10分钟内快速失败
        time.sleep(min(8, 0.8 * (i + 1)))
    raise last

def _json(r, **_ignore):
    """r.json() 的容错封装。忽略多余 kwargs, 防止误传参数导致静默失败。"""
    try:
        return r.json()
    except Exception:
        r.encoding = "utf-8"
        return r.json()

# ---------------- 全市场快照 ----------------
def _log_qt(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def _qt_limit(prev_close, code):
    """按板块由昨收推算涨停/跌停价。"""
    if prev_close in (None, 0): return None, None
    b = board_of(code)
    up_m = {"main": 1.10, "cy": 1.20, "kc": 1.20, "bj": 1.30}[b]
    dn_m = {"main": 0.90, "cy": 0.80, "kc": 0.80, "bj": 0.70}[b]
    return round(prev_close * up_m, 2), round(prev_close * dn_m, 2)

def fetch_universe_qt(batch=80, workers=8):
    """备用源: 腾讯 qt.gtimg.cn 按号段枚举全市场快照。
    字段: [1]名称 [2]代码 [3]现价 [4]昨收 [6]量(手) [32]涨跌% [38]换手% [49]量比。"""
    codes = []
    for pre in ("600", "601", "603", "605", "688"):
        codes += [f"sh{pre}{i:03d}" for i in range(1000)]
    for pre in ("000", "001", "002", "003", "300", "301"):
        codes += [f"sz{pre}{i:03d}" for i in range(1000)]
    batches = [codes[i:i + batch] for i in range(0, len(codes), batch)]
    def fetch_batch(b):
        try:
            r = _get("https://qt.gtimg.cn/q=" + ",".join(b),
                     headers={"Referer": "https://gu.qq.com/"}, timeout=8)
            r.encoding = "gbk"
            rows = []
            for seg in r.text.split(";"):
                if "~" not in seg: continue
                f = seg.split("~")
                if len(f) < 50: continue
                code = f[2].strip()
                if not re.fullmatch(r"\d{6}", code): continue
                name = f[1].strip()
                price, prev_close = _f(f[3]), _f(f[4])
                if price in (None, 0) or prev_close in (None, 0): continue
                pct = _f(f[32]); vol = _f(f[6]); amount_wan = _f(f[37])
                lu, ld = _qt_limit(prev_close, code)
                rows.append({"code": code, "name": name, "price": price, "pct": pct,
                             "vol": vol, "amount": amount_wan * 1e4 if amount_wan else None,
                             "turnover": _f(f[38]), "volratio": _f(f[49]),
                             "prev_close": prev_close, "limit_dn": ld, "limit_up": lu})
            return rows
        except Exception:
            return []
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(fetch_batch, batches):
            out.extend(rows)
    # 按代码去重(号段枚举天然唯一, 保险)
    seen, uniq = set(), []
    for x in out:
        if x["code"] in seen: continue
        seen.add(x["code"]); uniq.append(x)
    return uniq

def fetch_universe(page_size=200):
    """全市场快照: 主源东财, 被限流时自动切换腾讯枚举源。"""
    out, page = [], 1
    rounds = 0
    while True:
        try:
            d = _json(_get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={"pn": page, "pz": page_size, "po": "1", "np": "1", "fltt": "2", "invt": "2",
                        "fid": "f12", "fs": UNIVERSE_FS,
                        "fields": "f12,f14,f2,f3,f5,f6,f8,f10,f17,f15,f16"},
                headers={"Referer": "https://quote.eastmoney.com/"}))
        except Exception:
            rounds += 1
            if rounds > 2:
                _log_qt("东财快照持续失败, 切换腾讯枚举源")
                return fetch_universe_qt()
            time.sleep(15)  # 被限流时等待退避后重试当前页
            continue
        diff = (d.get("data") or {}).get("diff") or []
        total = (d.get("data") or {}).get("total", 0)
        for x in diff:
            out.append({
                "code": str(x.get("f12")), "name": x.get("f14", ""),
                "price": _f(x.get("f2")), "pct": _f(x.get("f3")),
                "vol": _f(x.get("f5")), "amount": _f(x.get("f6")),
                "turnover": _f(x.get("f8")), "volratio": _f(x.get("f10")),
                "prev_close": _f(x.get("f17")), "limit_dn": _f(x.get("f16")), "limit_up": _f(x.get("f15")),
            })
        if len(out) >= total or not diff:
            break
        page += 1
        if page > 60: break
    return out

def _f(v):
    try:
        if v in (None, "-", ""): return None
        return float(v)
    except (TypeError, ValueError):
        return None

# ---------------- 历史K线 ----------------
def fetch_kline_tx(code: str, days=120, mkt: str = None):
    """备用源: 腾讯行情日K(前复权)。东财K线被限流时自动切换。
    mkt: 显式指定 "sh"/"sz"(取指数时必须), 缺省按代码前缀推断。"""
    if mkt is None:
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 2.2 * 86400))
    end = time.strftime("%Y-%m-%d")
    try:
        d = _json(_get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{mkt}{code},day,{start},{end},{days},qfq"},
            headers={"Referer": "https://gu.qq.com/"}, timeout=8))
        node = (d.get("data") or {}).get(f"{mkt}{code}") or {}
        raw = node.get("qfqday") or node.get("day") or []
    except Exception:
        return []
    rows, prev = [], None
    for r in raw:
        if len(r) < 5: continue
        try:
            o, c, h, l = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        except (TypeError, ValueError):
            continue
        pct = round((c - prev) / prev * 100, 2) if prev else None
        rows.append({"date": r[0], "open": o, "close": c, "high": h, "low": l,
                     "vol": _f(r[5]) if len(r) > 5 else None, "amount": None,
                     "amplitude": None, "pct": pct, "turnover": None})
        prev = c
    return rows

def _quote_one(code: str, market: int = None):
    """单只实时快照(腾讯), 返回 dict 或 None。用于给滞后的日K补最新一根 + 校验基准。

    market: 1=沪(指数用 sh 前缀), 0=深; 缺省按代码前缀推断。
    """
    if market is None:
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    else:
        mkt = "sh" if market == 1 else "sz"
    try:
        r = _get(f"http://qt.gtimg.cn/q={mkt}{code}", timeout=6)
        r.encoding = "gbk"
        body = r.text.split('="')[-1].strip().strip(';"')
        p = body.split("~")
        if len(p) < 40:
            return None
        price = float(p[3])
        if price <= 0:           # 停牌/无成交
            return None
        return {"date": (p[30] or "")[:8], "name": p[1], "open": float(p[5] or 0),
                "close": price, "high": float(p[33] or 0), "low": float(p[34] or 0),
                "vol": _f(p[6]), "pct": _f(p[32]), "turnover": _f(p[38]), "prev_close": float(p[4])}
    except Exception:
        return None

def _patch_last_bar(code: str, rows, q=None):
    """K线滞后时, 用实时快照补上最新一根日K(仅当价格基准一致, 避免复权错配)。"""
    if not rows:
        return rows
    if q is None:
        q = _quote_one(code)
    if not q:
        return rows
    today = time.strftime("%Y-%m-%d")
    if rows[-1]["date"] >= today:
        return rows
    last = rows[-1]["close"]
    if not last or abs(q["prev_close"] - last) / last > 0.02:   # 基准不一致(除权等)则不补
        return rows
    prev_close = last
    rows = rows + [{"date": today, "open": q["open"], "close": q["close"], "high": q["high"],
                    "low": q["low"], "vol": q["vol"], "amount": None, "amplitude": None,
                    "pct": round((q["close"] - prev_close) / prev_close * 100, 2),
                    "turnover": q["turnover"]}]
    return rows

def _kline_sane(rows, q) -> bool:
    """用实时快照校验K线基准: 防止取错标的 / 后复权错配(数值差量级)。

    快照取不到(停牌等)时视为通过, 不做判断。
    """
    if not q or not rows:
        return True
    last = rows[-1].get("close")
    ref = q.get("prev_close") or q.get("close")
    if not last or not ref:
        return True
    return abs(ref - last) / last <= 0.15

def fetch_kline(code: str, days=120, market: int = None):
    """多源K线: 东财 -> 腾讯 -> 同花顺 -> 通达信(券商协议, 可选)。

    每个源的结果先用实时快照校验基准(防取错标的/复权错配), 通过后才补齐最新一根;
    全军覆没时返回 [] (宁缺勿错, 由调用方按跳过处理)。
    market 缺省按代码前缀推断(1=沪, 0=深), 取上证指数等需显式传 market=1。
    返回按日期升序 list[dict]。
    """
    if market is None:
        market = market_of(code)
    mkt = "sh" if market == 1 else "sz"
    q = _quote_one(code, market)                 # 一次快照: 校验 + 补最新一根共用
    for rows in (_fetch_kline_em(code, days, market), fetch_kline_tx(code, days, mkt),
                 _fetch_kline_ths(code, days), _fetch_kline_tdx(code, days)):
        if not rows:
            continue
        if _kline_sane(rows, q):
            return _patch_last_bar(code, rows, q)
        # 被拒的源不落数据(拒绝静默, 避免刷屏)
    return []


def _fetch_kline_ths(code, days=120):
    """第三源: 同花顺(独立上游)。失败静默返回 []。"""
    try:
        from . import sources_ths
        return sources_ths.fetch_kline_ths(code, days)
    except Exception:
        return []

def _fetch_kline_tdx(code, days=120):
    """第三源: 通达信协议(长江证券等券商同协议)。未装 pytdx 或连不通时静默返回 []。"""
    try:
        from . import sources_tdx
        return sources_tdx.fetch_kline_tdx(code, days)
    except Exception:
        return []

def _fetch_kline_em(code: str, days=120, market: int = 1):
    """东财历史K线(原 fetch_kline 主体)。"""
    secid = f"{market}.{code}"
    beg = time.strftime("%Y%m%d", time.localtime(time.time() - days * 2.2 * 86400))
    try:
        d = _json(_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={"ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101", "fqt": "1", "beg": beg, "end": "20500101", "l": str(days), "secid": secid},
            headers={"Referer": "https://quote.eastmoney.com/"}, timeout=8))
        node = d.get("data") or {}
        kl = node.get("klines") or []
    except Exception:
        return []
    # secid 市场前缀写错时, 东财可能返回同代码的其它标的(如 1.000002=上证A股指数),
    # 用返回体里的 code 反查, 不一致直接弃用, 让调用方落到下一个源。
    if kl and str(node.get("code") or "").strip().lstrip("0") != str(code).lstrip("0"):
        return []
    rows = []
    for s in kl:
        p = s.split(",")
        if len(p) < 11: continue
        # fields2: f51日期,f52开,f53收,f54高,f55低,f56量,f57额,f58振幅,f59涨跌幅,f60涨跌额,f61换手
        rows.append({"date": p[0], "open": _f(p[1]), "close": _f(p[2]), "high": _f(p[3]),
                     "low": _f(p[4]), "vol": _f(p[5]), "amount": _f(p[6]),
                     "amplitude": _f(p[7]), "pct": _f(p[8]), "turnover": _f(p[10])})
    return rows

def market_of(code: str) -> int:
    return 1 if code.startswith(("6", "9", "5")) else 0

def fetch_klines_parallel(codes, days=120, workers=10):
    """并行抓取K线, 返回 {code: rows}。"""
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_kline, c, days, market_of(c)): c for c in codes}
        for f in as_completed(futs):
            c = futs[f]
            try:
                out[c] = f.result()
            except Exception:
                out[c] = []
    return out

# ---------------- 指数/市场情绪 ----------------
def fetch_indices():
    """指数快照: 主源东财, 失败自动切腾讯批量行情。"""
    try:
        d = _json(_get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"fltt": "2", "invt": "2", "fields": "f12,f14,f2,f3,f5,f6",
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                    "secids": "1.000001,0.399001,0.399006,1.000300"},
            headers={"Referer": "https://quote.eastmoney.com/"}))
        return [{"code": x["f12"], "name": x["f14"], "price": _f(x.get("f2")), "pct": _f(x.get("f3"))}
                for x in (d.get("data") or {}).get("diff") or []]
    except Exception:
        return fetch_indices_qt()

def fetch_indices_qt():
    """备用源: 腾讯批量行情拉指数(上证/深成/创业板/沪深300)。"""
    out = []
    try:
        r = _get("https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000300",
                 headers={"Referer": "https://gu.qq.com/"}, timeout=8)
        r.encoding = "gbk"
        name_map = {"sh000001": "上证指数", "sz399001": "深证成指",
                    "sz399006": "创业板指", "sh000300": "沪深300"}
        for seg in r.text.split(";"):
            if "~" not in seg: continue
            f = seg.split("~")
            sym = f[0].split("=")[0].replace("v_", "").strip() if "=" in f[0] else ""
            out.append({"code": sym[-6:], "name": name_map.get(sym, sym[-6:]),
                        "price": _f(f[3]), "pct": _f(f[32])})
    except Exception:
        pass
    return out

# ---------------- 板块热度 ----------------
def fetch_sectors(top_n=40):
    try:
        d = _json(_get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={"pn": "1", "pz": str(top_n), "po": "1", "np": "1", "fltt": "2", "invt": "2",
                    "fid": "f3", "fs": "m:90 t:2", "fields": "f12,f14,f3,f62"},
            headers={"Referer": "https://quote.eastmoney.com/"}))
        return [{"code": x["f12"], "name": x["f14"], "pct": _f(x.get("f3"))}
                for x in (d.get("data") or {}).get("diff") or []]
    except Exception:
        return []   # 云端/限流时板块热度缺失, 特征置空不阻塞

# ---------------- 候选预筛(打板活跃池) ----------------
def screen_candidates(universe, max_n=400):
    """从全市场快照中筛出次日打板候选池:
    (A) 今日已涨停(连板潜力) + (B) 强势未涨停(临近涨停/大涨)
    过滤: 换手 3%~25%, 价格>=3元, 排除 ST/退市/新股(N/C)。
    按 热度=涨幅×量比×换手 排序, 返回前 max_n 只。
    """
    def heat(x):
        pct = x.get("pct") or 0
        tr = x.get("turnover") or 0
        vr = x.get("volratio") or 0
        return pct * (1 + min(vr, 5) * 0.15) * min(1.0, tr / 8) * (1 if tr < 20 else 0.7)
    out = []
    for x in universe:
        code, name = x["code"], x.get("name") or ""
        pct = x.get("pct")
        if pct is None: continue
        if name.startswith(("N", "C")) or "ST" in name.upper() or "退" in name: continue
        if (x.get("price") or 0) < 3: continue
        tr = x.get("turnover") or 0
        if tr < 3 or tr > 25: continue
        b = board_of(code)
        thr = LIMIT_PCT[b]
        if is_limit_up(pct, code):
            pass  # 今日涨停 -> 连板候选
        elif b == "main":
            if not (3.5 <= pct < thr): continue
        elif b in ("cy", "kc"):
            if not (6.0 <= pct < thr): continue
        else:  # bj
            if not (7.0 <= pct < thr): continue
        x["_heat"] = heat(x)
        out.append(x)
    out.sort(key=lambda x: -x["_heat"])
    return out[:max_n]

# ---------------- 个股所属板块 ----------------
def fetch_stock_board(code: str) -> str:
    """返回行业名, 失败返回 ''。"""
    try:
        d = _json(_get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"fltt": "2", "invt": "2", "fields": "f127,f57,f58",
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b", "secid": f"{market_of(code)}.{code}"},
            headers={"Referer": "https://quote.eastmoney.com/"}, timeout=6))
        return (d.get("data") or {}).get("f127") or ""
    except Exception:
        return ""

def fetch_stock_boards(codes, workers=12):
    """并行返回 {code: industry_name}。"""
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_stock_board, c): c for c in codes}
        for f in as_completed(futs):
            c = futs[f]
            try:
                out[c] = f.result()
            except Exception:
                out[c] = ""
    return out

# ---------------- 新浪7x24 资讯 ----------------
def fetch_news(pages=3, per_page=50, max_age_hours=36):
    """返回 list[dict]: time, text, stocks:[(code,name)]。"""
    items = []
    for p in range(1, pages + 1):
        try:
            r = S.get("https://zhibo.sina.com.cn/api/zhibo/feed",
                      params={"page": p, "page_size": per_page, "zhibo_id": "152",
                              "tag_id": "0", "dire": "f", "dpc": "1"},
                      headers={"Referer": "https://finance.sina.com.cn/"}, timeout=10)
            lst = (r.json().get("result") or {}).get("data", {}).get("feed", {}).get("list") or []
        except Exception:
            break
        if not lst: break
        for x in lst:
            text = (x.get("rich_text") or "").strip()
            if not text: continue
            stocks = []
            ext = x.get("ext")
            if ext:
                try:
                    for st in json.loads(ext).get("stocks") or []:
                        sym = st.get("symbol") or st.get("code") or ""
                        if re.fullmatch(r"\d{6}", sym):
                            stocks.append((sym, st.get("name", "")))
                except Exception:
                    pass
            items.append({"time": x.get("create_time", ""), "text": text, "stocks": stocks})
        time.sleep(0.2)
    return items

def news_heat_for(code: str, name: str, items) -> dict:
    """统计个股相关资讯: 数量/最近时间/样本。名称须精确出现或ext关联。"""
    n = 0; last = ""; samples = []
    for it in items:
        if any(c == code for c, _ in it["stocks"]) or (name and len(name) >= 3 and name in it["text"]):
            n += 1
            if it["time"] > last: last = it["time"]
            if len(samples) < 2: samples.append(it["text"][:60])
    return {"n": n, "last_time": last, "samples": samples}

# ---------------- 自测 ----------------
if __name__ == "__main__":
    u = fetch_universe()
    zt = [x for x in u if is_limit_up(x["pct"], x["code"]) and not x["name"].startswith(("N", "C"))]
    print("universe:", len(u), " limit-up:", len(zt))
    print("indices:", fetch_indices())
    print("sectors[:3]:", fetch_sectors(3))
    kl = fetch_kline("600519", 10)
    print("kline 600519 tail:", kl[-1] if kl else "EMPTY")
    news = fetch_news(1, 20)
    print("news:", len(news), news[0]["text"][:50] if news else "-")
