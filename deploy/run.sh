#!/usr/bin/env bash
# 定时运行入口: 交易日守卫在 main.py 内置(非交易日自动跳过)
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/$(date +%Y%m%d).log"
echo "===== $(date '+%F %T') run start =====" >> "$LOG"
./.venv/bin/python main.py >> "$LOG" 2>&1
echo "===== $(date '+%F %T') run end (exit $?) =====" >> "$LOG"
# 可选: 把最新报告推送到指定邮箱(需要服务器可发信, 配置后取消注释)
# ./.venv/bin/python deploy/notify.py >> "$LOG" 2>&1
