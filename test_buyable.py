# -*- coding: utf-8 -*-
"""隔离单元测试: 可买入(未封涨停)判定 + 推荐上限。
不依赖网络, 用手造快照验证边界。
"""
import sys
sys.path.insert(0, '.')
from daban import data

FAIL = []

def case(desc, got, exp):
    ok = got == exp
    print(f"{'PASS' if ok else 'FAIL'}  {desc:<52} got={got} exp={exp}")
    if not ok:
        FAIL.append(desc)

# ---- limit_up_price: 各板块 ----
case("主板 昨收10.00 -> 涨停价", data.limit_up_price("600000", 10.00), 11.00)
case("创业板 昨收20.00 -> 涨停价", data.limit_up_price("300001", 20.00), 24.00)
case("科创板 昨收50.00 -> 涨停价", data.limit_up_price("688001", 50.00), 60.00)
case("北交所 昨收10.00 -> 涨停价", data.limit_up_price("830001", 10.00), 13.00)
case("缺昨收 -> None", data.limit_up_price("600000", None), None)

# ---- is_sealed: 封板(买不进) ----
case("主板 现价=涨停价 -> 封板",
     data.is_sealed({"code": "600000", "price": 11.00, "prev_close": 10.00, "pct": 10.0}), True)
case("主板 现价高出涨停价(异常) -> 封板",
     data.is_sealed({"code": "600000", "price": 11.02, "prev_close": 10.00, "pct": 10.2}), True)
case("创业板 现价=涨停价 -> 封板",
     data.is_sealed({"code": "300001", "price": 24.00, "prev_close": 20.00, "pct": 20.0}), True)
case("四舍五入边界(昨收3.35) 现价=交易所涨停价3.69 -> 封板",
     data.is_sealed({"code": "600000", "price": 3.69, "prev_close": 3.35, "pct": 10.15}), True)

# ---- is_sealed: 可买(未封板) ----
case("主板 现价差涨停价1分 -> 可买",
     data.is_sealed({"code": "600000", "price": 10.99, "prev_close": 10.00, "pct": 9.9}), False)
case("主板 涨9.8% 但价未到涨停 -> 可买(不误杀)",
     data.is_sealed({"code": "600000", "price": 10.98, "prev_close": 10.00, "pct": 9.8}), False)
case("主板 涨5% -> 可买",
     data.is_sealed({"code": "600000", "price": 10.50, "prev_close": 10.00, "pct": 5.0}), False)
case("创业板 涨15% 未封板 -> 可买",
     data.is_sealed({"code": "300001", "price": 23.00, "prev_close": 20.00, "pct": 15.0}), False)

# ---- is_sealed: 缺字段兜底(用涨幅阈值) ----
case("缺 price/prev_close, 涨幅10% -> 封板(兜底)",
     data.is_sealed({"code": "600000", "pct": 10.0}), True)
case("缺 price/prev_close, 涨幅5% -> 可买(兜底)",
     data.is_sealed({"code": "600000", "pct": 5.0}), False)

print()
print("=" * 60)
print(f"{'ALL PASS' if not FAIL else 'FAILED: ' + str(FAIL)}  (共 {len(FAIL)} 项失败)")
sys.exit(1 if FAIL else 0)
