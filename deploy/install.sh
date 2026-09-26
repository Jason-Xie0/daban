#!/usr/bin/env bash
# A股打板预测工具 - Linux 服务器一键安装(在 deploy/ 目录内执行或直接 bash install.sh)
set -e
cd "$(dirname "$0")/.."
echo "==> 工作目录: $(pwd)"
python3 -m venv .venv
./.venv/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r deploy/requirements.txt
echo "==> 依赖安装完成"
echo "==> 服务器时间: $(date '+%F %T %Z')"
echo "    (crontab 按此时区; 若为 UTC, 14:30 北京时间 = 06:30 UTC)"
echo "==> 安装完成。下一步: 把本目录整体拷贝到服务器(含 state/ 样本库与模型), 再按 crontab.txt 配置定时。"
