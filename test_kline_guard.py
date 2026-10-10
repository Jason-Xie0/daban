"""K线基准校验(_kline_sane / fetch_kline 选源)离线回归测试。

覆盖 2026-09-29 事故场景: 某源返回"非本标的/后复权"序列(数值差量级),
必须被拒绝并落到正确源; 全军覆没时返回空, 绝不落错误数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daban import data  # noqa: E402

OK = [{"date": "2026-09-29", "close": 11.13, "high": 11.13, "low": 11.1,
       "open": 11.0, "vol": 1, "pct": 10.0, "amount": None, "amplitude": None, "turnover": None}]
BAD = [dict(OK[0], close=6931.26)]                    # 事故里的"深物业A"错误序列
QUOTE = {"date": "20260930", "name": "深物业A", "open": 11.2, "close": 12.24,
         "high": 12.24, "low": 11.2, "vol": 1, "pct": 9.97, "turnover": 1.0,
         "prev_close": 11.13}

cases = []


def case(fn):
    cases.append(fn)
    return fn


@case
def test_sane_rejects_wrong_scale():
    assert data._kline_sane(BAD, QUOTE) is False, "量级错误的序列必须被拒"


@case
def test_sane_accepts_normal():
    assert data._kline_sane(OK, QUOTE) is True


@case
def test_sane_without_quote():
    assert data._kline_sane(BAD, None) is True, "无快照时不做判断"


@case
def test_fetch_falls_through_to_sane_source():
    orig_em, orig_tx, orig_ths, orig_tdx, orig_q = (
        data._fetch_kline_em, data.fetch_kline_tx, data._fetch_kline_ths,
        data._fetch_kline_tdx, data._quote_one)
    try:
        data._fetch_kline_em = lambda c, d, m: list(BAD)      # 坏源(复权错配)
        data.fetch_kline_tx = lambda c, d, mkt=None: list(OK)  # 好源
        data._fetch_kline_ths = lambda c, d: []
        data._fetch_kline_tdx = lambda c, d: []
        data._quote_one = lambda c, m=None: dict(QUOTE)
        rows = data.fetch_kline("000011", 30)
        # 注: _patch_last_bar 会用快照补最新一根, 故只校验历史段拿到的是正常源
        assert rows and abs(rows[0]["close"] - 11.13) < 1e-6, f"应落到正常源, 实际 {rows[:1]}"
    finally:
        (data._fetch_kline_em, data.fetch_kline_tx, data._fetch_kline_ths,
         data._fetch_kline_tdx, data._quote_one) = (
            orig_em, orig_tx, orig_ths, orig_tdx, orig_q)


@case
def test_fetch_returns_empty_when_all_bad():
    orig = (data._fetch_kline_em, data.fetch_kline_tx, data._fetch_kline_ths,
            data._fetch_kline_tdx, data._quote_one)
    try:
        data._fetch_kline_em = lambda c, d, m: list(BAD)
        data.fetch_kline_tx = lambda c, d, mkt=None: list(BAD)
        data._fetch_kline_ths = lambda c, d: list(BAD)
        data._fetch_kline_tdx = lambda c, d: list(BAD)
        data._quote_one = lambda c, m=None: dict(QUOTE)
        assert data.fetch_kline("000011", 30) == [], "全部不合格时应返回空而非脏数据"
    finally:
        (data._fetch_kline_em, data.fetch_kline_tx, data._fetch_kline_ths,
         data._fetch_kline_tdx, data._quote_one) = orig


@case
def test_default_market_follows_code():
    """深市代码缺省应走 0(深), 沪市走 1 —— 这是本次事故的根因。"""
    seen = []
    orig = (data._fetch_kline_em, data._quote_one, data.fetch_kline_tx,
            data._fetch_kline_ths, data._fetch_kline_tdx)
    try:
        def fake_em(c, d, m):
            seen.append((c, m))
            return list(OK)
        data._fetch_kline_em = fake_em
        data._quote_one = lambda c, m=None: dict(QUOTE)
        data.fetch_kline_tx = lambda c, d, mkt=None: []
        data._fetch_kline_ths = lambda c, d: []
        data._fetch_kline_tdx = lambda c, d: []
        data.fetch_kline("000011", 30)
        data.fetch_kline("600519", 30)
        data.fetch_kline("300750", 30)
        assert seen == [("000011", 0), ("600519", 1), ("300750", 0)], seen
    finally:
        (data._fetch_kline_em, data._quote_one, data.fetch_kline_tx,
         data._fetch_kline_ths, data._fetch_kline_tdx) = orig


if __name__ == "__main__":
    fail = 0
    for f in cases:
        try:
            f()
            print(f"PASS {f.__name__}")
        except AssertionError as e:
            fail += 1
            print(f"FAIL {f.__name__}: {e}")
    print(f"\n{len(cases) - fail}/{len(cases)} 通过")
    sys.exit(1 if fail else 0)
