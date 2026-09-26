#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把最新打板预测报告推送到手机(微信)。

通道二选一, 用环境变量 DABAN_PUSH_KEY 区分:
  - Server酱 Turbo: key 以 SCT 开头  -> 微信服务号消息  (https://sct.ftqq.com 微信扫码登录即可拿 SendKey)
  - PushPlus:       其他任意 token   -> 微信公众号推送  (https://www.pushplus.plus 微信登录拿 token)

GitHub Secrets 里配一个 DABAN_PUSH_KEY 即可, 无其他配置。
用法: python deploy/notify.py reports/latest.md
"""
import os
import re
import sys

import requests

S = requests.Session()
S.trust_env = False


def load_report(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def build_digest(md: str, top_n: int = 10) -> str:
    """从报告中截取 标题 + TopN 候选, 控制在推送长度内。"""
    lines = md.splitlines()
    title = ""
    body: list[str] = []
    started = False
    for ln in lines:
        if not title and ln.startswith("# "):
            title = ln[2:].strip()
        if re.match(r"^#{1,3}\s*(TOP|Top|top)", ln) or "TOP 预测" in ln:
            started = True
        if started:
            body.append(ln)
    if not body:  # 兜底: 直接取前 60 行
        body = lines[:60]
    text = "\n".join(body).strip()
    if len(text) > 2800:  # 各通道对 content 长度都有限制, 截断
        text = text[:2800] + "\n\n...(完整报告见 GitHub 仓库 reports/latest.md)"
    return title or "打板预测报告", text


def push_sct(key: str, title: str, desp: str) -> bool:
    r = S.post(f"https://sctapi.ftqq.com/{key}.send",
               data={"title": title[:32], "desp": desp}, timeout=20)
    ok = r.status_code == 200 and r.json().get("code") == 0
    print(f"[notify] Server酱 -> {'OK' if ok else 'FAIL ' + r.text[:200]}")
    return ok


def push_pushplus(token: str, title: str, content: str) -> bool:
    r = S.post("https://www.pushplus.plus/send",
               json={"token": token, "title": title[:100],
                     "content": content, "template": "markdown"}, timeout=20)
    ok = r.status_code == 200 and r.json().get("code") == 200
    print(f"[notify] PushPlus -> {'OK' if ok else 'FAIL ' + r.text[:200]}")
    return ok


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "reports/latest.md"
    if not os.path.exists(path):
        print(f"[notify] 报告不存在: {path}, 跳过推送")
        return 0
    key = os.environ.get("DABAN_PUSH_KEY", "").strip()
    if not key:
        print("[notify] 未配置 DABAN_PUSH_KEY, 跳过推送")
        return 0
    title, text = build_digest(load_report(path))
    if key.upper().startswith("SCT"):
        ok = push_sct(key, title, text)
    else:
        ok = push_pushplus(key, title, text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
