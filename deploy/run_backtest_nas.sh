#!/usr/bin/env bash
# 股票回测批处理 — 在 NAS 上运行
# 用法:  ./run_backtest.sh            仅跑走前回测(用现有样本库)
#        ./run_backtest.sh --rebuild  先重建样本库(抓最新行情)再回测
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY=./venv/bin/python
if [ ! -x "$PY" ]; then
  echo "!! 找不到 venv: $(pwd)/venv"
  exit 1
fi
LOG_DIR=logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/backtest_$(date +%Y%m%d).log"

{
  echo "===== $(date '+%F %T') 开始 (参数: ${1:-无}) ====="
  if [ "${1:-}" = "--rebuild" ]; then
    echo "--- 1/2 重建样本库(全市场快照 + 600只K线) ---"
    $PY backfill_history.py || { echo "!! backfill 失败, 中止"; exit 1; }
  fi
  echo "--- 2/2 走前回测 + 全库重训 ---"
  $PY walkforward.py || { echo "!! walkforward 失败, 中止"; exit 1; }
  echo "===== $(date '+%F %T') 完成 ====="
} >> "$LOG" 2>&1

echo "日志: $LOG"
grep -E '样本库完成|样本 |检查点 |战绩档案|模型: |用时 |DONE|失败' "$LOG" | tail -12
