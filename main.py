# -*- coding: utf-8 -*-
"""命令行入口: 运行A股次日涨停预测。
用法: python main.py [--train] [--force]
  --train  强制重训;  --force 跳过交易日守卫(节假日也跑)
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def is_trading_day_today():
    """交易日守卫: 上证指数日K最新一根是否为今日。探测失败时保守放行。"""
    try:
        from daban import data
        kl = data.fetch_kline("000001", 5, market=1)  # secid 1.000001 = 上证指数
        return bool(kl) and kl[-1]["date"] == time.strftime("%Y-%m-%d")
    except Exception:
        return True

if __name__ == "__main__":
    force = "--train" in sys.argv or "--force" in sys.argv
    if "--force" not in sys.argv and not is_trading_day_today():
        print(f"[{time.strftime('%H:%M:%S')}] 今日非交易日(或数据未更新), 跳过预测。(--force 可强制)")
        sys.exit(0)
    from daban.run import run
    result = run(force_train=force)
    if result:
        print("\n==== TOP 预览 ====")
        for i, s in enumerate(result["top"][:12], 1):
            print(f"{i:>2}. {s['code']} {s['name']:<8} {s['prob']:.1%}  {s['reason']}")
