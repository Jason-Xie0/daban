# -*- coding: utf-8 -*-
"""主编排: A股次日涨停(打板)预测。
流程: 模型(训练/加载) -> 实时快照+候选池 -> K线+行业 -> 服务特征 -> 模型打分 -> 大模型理由 -> 报告
含【上下文压缩】检查点: 每个阶段落盘 state/checkpoint.json, 会话可断点续跑。
"""
import json, os, time, random, sys

from . import data, features, model, reasoner, report, feedback, market, sentiment

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE_DIR, "state")
CKPT = os.path.join(STATE_DIR, "checkpoint.json")
TOP_N = 30            # 内部保留候选数(落池/生成理由用)
RECOMMEND_N = 10      # 对外推荐上限(用户要求: 只推可买入的, 且不超过 10 只)
CANDIDATE_POOL = 400
TRAIN_UNIVERSE = 600
TRAIN_DAYS = 120
MARKET_SNAP = {}   # 本次运行时的大盘要素(供报告展示)

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)

def ckpt_save(phase, extra=None):
    """上下文压缩检查点: 把当前阶段与关键中间产物落盘, 供断点续跑。
    检查点是辅助产物, 写失败(偶发 PermissionError)绝不能中断主流程。"""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        d = {"phase": phase, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "context_bytes_approx": len(json.dumps(extra or {}, ensure_ascii=False, default=str))}
        if extra: d.update(extra)
        with open(CKPT, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2, default=str)
        log(f"⏬ 上下文压缩检查点 -> {phase} (约 {d['context_bytes_approx']} 字节)")
    except Exception as e:
        log(f"⚠️ 检查点写入失败(不影响主流程): {phase} {e}")

# ============ 阶段1: 模型 ============
TRAIN_CACHE = os.path.join(STATE_DIR, "train_cache.pkl")

def _cache_load():
    try:
        import pickle
        with open(TRAIN_CACHE, "rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None

def _cache_save(rows, y):
    try:
        import pickle
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(TRAIN_CACHE, "wb") as fh:
            pickle.dump({"rows": rows, "y": y,
                         "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, fh)
    except Exception as e:
        log(f"  (训练缓存保存失败: {e})")

def _fetch_train_rows():
    """抓取训练宇宙 K 线并构造 (rows, y)。"""
    snap = data.fetch_universe()
    liquid = [x for x in snap if (x.get("turnover") or 0) >= 2
              and (x.get("price") or 0) >= 3
              and "ST" not in (x.get("name") or "").upper()
              and not (x.get("name") or "").startswith(("N", "C"))]
    liquid.sort(key=lambda x: -(x.get("turnover") or 0))
    codes = [x["code"] for x in liquid[:TRAIN_UNIVERSE]]
    log(f"  训练宇宙: {len(codes)} 只 (共 {len(snap)} 只快照, 流动性筛选)")
    kmap = data.fetch_klines_parallel(codes, days=TRAIN_DAYS, workers=12)
    ok = sum(1 for v in kmap.values() if len(v) >= 30)
    log(f"  抓取K线: {ok}/{len(codes)} 只有效(>=30日)")
    rows, y = features.build_training_rows(kmap)
    log(f"  训练样本: {len(rows)} 条, 触板正样本 {sum(y)} (基线 {sum(y)/max(1,len(rows)):.3%})")
    return rows, y

def ensure_model(force_train=False):
    need_flag = os.path.join(STATE_DIR, "NEED_RETRAIN")
    if os.path.exists(need_flag):
        log("检测到待重训标记(NEED_RETRAIN), 本次强制重训")
        force_train = True
    m = None if force_train else model.load_model()
    # 特征槽变更守卫: 模型训练时的特征列表与当前 CORE_FEATURES 不一致 -> 必须重训
    if m:
        old_feats = m.get("features")
        if old_feats and list(old_feats) != list(features.CORE_FEATURES):
            log(f"⚠️ 特征槽已变更({len(old_feats)} -> {len(features.CORE_FEATURES)}), 强制重训")
            force_train = True
            m = None
        elif not old_feats and len(m.get("lr_coef", [[]])[0]) != len(features.CORE_FEATURES):
            log("⚠️ 模型特征维度与当前特征槽不一致, 强制重训")
            force_train = True
            m = None
    if m and m.get("n_samples", 0) > 2000:
        log(f"✔ 使用已有模型: 样本 {m['n_samples']}, 触板率 {m['base_rate']:.3%}, 训练于 {m['trained_at']}")
        # 复用模型: 若缺回测, 用训练缓存补算
        if not m.get("backtest"):
            cache = _cache_load()
            if cache:
                bt = model.ensure_backtest(m, cache["rows"], cache["y"])
                log(f"  回测(缓存补算): {bt}")
            else:
                log("  (无训练缓存, 跳过回测补算)")
        return m, m.get("backtest")
    log("阶段1: 训练模型(滚动样本库) ...")
    ckpt_save("train_start")
    lib_rows, lib_y = feedback.load_samples()
    if len(lib_rows) >= 8000:
        rows, y = lib_rows, lib_y
        log(f"  使用滚动样本库: {len(rows)} 条, 触板正样本 {sum(y)} (离线训练, 无需抓取)")
    else:
        log(f"  样本库不足({len(lib_rows)}条), 抓取训练宇宙回填 ...")
        try:
            base_rows, base_y = _fetch_train_rows()
        except Exception as e:
            log(f"⚠️ 训练数据抓取失败({e}), 尝试回退已有模型")
            m_old = model.load_model()
            if m_old and m_old.get("n_samples", 0) > 2000:
                log(f"✔ 回退使用已有模型: 样本 {m_old['n_samples']}, 训练于 {m_old['trained_at']}")
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(os.path.join(STATE_DIR, "NEED_RETRAIN"), "w", encoding="utf-8") as fh:
                    fh.write(time.strftime("%Y-%m-%d %H:%M:%S"))
                return m_old, m_old.get("backtest")
            raise
        feedback.append_pairs(base_rows, base_y)
        rows, y = feedback.load_samples()
        log(f"  样本库回填完成: {len(rows)} 条")
    ckpt_save("train_rows", {"n_rows": len(rows), "n_pos": sum(y)})
    # 大盘要素注入(指数真实历史 + 样本宇宙宽度代理); 失败则填中性值, 不阻塞训练
    try:
        imap = market.index_map(400)
        bmap = market.daily_breadth(rows)
        market.attach(rows, imap, bmap)
        log(f"  大盘要素已注入: 指数 {len(imap)} 日, 宽度 {len(bmap)} 日")
    except Exception as e:
        log(f"⚠️ 大盘要素注入失败(按中性值处理): {e}")
        market.attach(rows)
    _cache_save(rows, y)
    m = model.train(rows, y, save=True)
    bt = model.backtest(rows, y, k_list=(5, 10, 20))
    log(f"  回测: {bt}")
    ckpt_save("model_ready", {"n_samples": m["n_samples"], "auc": m.get("auc"), "backtest": bt})
    flag = os.path.join(STATE_DIR, "NEED_RETRAIN")
    if os.path.exists(flag):
        os.remove(flag)
        log("✔ 重训完成, 已清除 NEED_RETRAIN 标记")
    return m, bt

# ============ 阶段2: 实时快照 + 候选池 ============
def build_candidates():
    log("阶段2: 抓取全市场实时快照 + 预筛打板候选池 ...")
    snap = data.fetch_universe()
    cands = data.screen_candidates(snap, max_n=CANDIDATE_POOL)
    zt = [x for x in snap if data.is_limit_up(x["pct"], x["code"])
          and not (x.get("name") or "").startswith(("N", "C"))]
    log(f"  全市场 {len(snap)} 只 | 今日涨停 {len(zt)} | 候选池 {len(cands)}")
    ckpt_save("candidates", {"n_universe": len(snap), "n_zt": len(zt), "n_cand": len(cands),
                             "codes": [c["code"] for c in cands[:50]]})
    return snap, cands, zt

# ============ 阶段3: K线 + 行业 + 资讯 -> 服务特征 + 打分 ============
def score_candidates(cands, news_items, snap=None, indices=None):
    codes = [c["code"] for c in cands]
    log("阶段3a: 并行抓取候选K线 ...")
    kmap = data.fetch_klines_parallel(codes, days=90, workers=12)
    ckpt_save("cand_klines", {"n_ok": sum(1 for v in kmap.values() if v)})
    log("阶段3b: 并行抓取行业名 ...")
    boards = data.fetch_stock_boards(codes, workers=12)
    sector_pct = {s["name"]: s["pct"] for s in data.fetch_sectors(60)}
    mk = {}
    try:
        mk = market.today(snap, indices)
        MARKET_SNAP.clear(); MARKET_SNAP.update(mk)
        log(f"  大盘要素: 上证 {mk['idx_pct']:+.2f}% | MA5偏离 {mk['idx_ma5_dev']:+.2%} | "
            f"5日累计 {mk['idx_mom5']:+.2f}% | 上涨占比 {mk['breadth_up']:.0%} | 涨停占比 {mk['zt_share']:.2%}")
    except Exception as e:
        log(f"⚠️ 大盘要素获取失败(按中性值处理): {e}")
    # 舆情打分(真实新闻关键词情感, 只算候选股)
    sent_map = {}
    try:
        sent_map = sentiment.analyze(news_items,
                                     {c["code"]: c.get("name") for c in cands})
        n_hot = sum(1 for v in sent_map.values() if v["heat"] > 0)
        log(f"  舆情: {len(news_items)} 条资讯扫描完成, 候选中 {n_hot} 只有相关舆情")
    except Exception as e:
        log(f"⚠️ 舆情打分失败(按无舆情处理): {e}")
    log("阶段3c: 计算服务特征 + 模型打分 ...")
    rows, meta = [], []
    for c in cands:
        code = c["code"]
        kl = kmap.get(code) or []
        # hist 截至昨日(排除今日, 今日用快照)
        today = time.strftime("%Y-%m-%d")
        hist = [r for r in kl if r.get("date") != today]
        snapc = dict(c)
        snapc["_sector_pct"] = sector_pct.get(boards.get(code))
        nh = data.news_heat_for(code, c.get("name"), news_items)
        snapc["_news_n"] = nh["n"]
        snapc["_news_samples"] = nh.get("samples")
        sv = sent_map.get(code) or {}
        snapc["_sent_net"] = sv.get("net", 0)
        snapc["_sent_heat"] = sv.get("heat", 0)
        snapc["_sent"] = sv
        f = features.serve_features(code, hist, snapc)
        if f is None: continue
        f.update(mk)                       # 大盘要素: 当日全市场同一组值
        f["_board_name"] = boards.get(code, "")
        rows.append(f)
        meta.append({"code": code, "name": c.get("name"), "snap": snapc, "news": nh, "sent": sv})
    if not rows:
        log("  无有效特征(可能K线不足), 跳过打分")
        return []
    m = model.load_model()
    probs = model.predict_proba(m, rows)
    scored = []
    for f, mt, p in zip(rows, meta, probs):
        scored.append({"code": mt["code"], "name": mt["name"], "prob": p,
                       "price": mt["snap"].get("price"),
                       "features": f, "news": mt.get("news"), "sent": mt.get("sent") or {},
                       "today_pct": mt["snap"].get("pct"), "board": f.get("board"),
                       "sealed": data.is_sealed(mt["snap"]),   # 预测时刻已封涨停(买不到)
                       "industry": mt.get("snap", {}).get("_board_name")})
    scored.sort(key=lambda x: -x["prob"])
    top = scored[:TOP_N]
    log(f"  打分完成: {len(scored)} 只有效, Top1 = {top[0]['name']}({top[0]['code']}) {top[0]['prob']:.1%}")
    ckpt_save("scored", {"n": len(scored),
                         "top5": [{"code": x["code"], "name": x["name"], "prob": round(x["prob"], 3)} for x in top[:5]]})
    return scored

# ============ 阶段4: 大模型理由 ============
def attach_reasons(top):
    log("阶段4: 大模型生成简要理由(仅基于真实特征与资讯) ...")
    ckpt_save("reason_start", {"n": len(top)})
    llm_reasons = reasoner.reason_with_llm(top)
    n_llm = 0
    for s in top:
        r = llm_reasons.get(s["code"])
        if r:
            s["reason"] = r; s["reason_source"] = "llm"; n_llm += 1
        else:
            s["reason"] = reasoner.template_reason(s); s["reason_source"] = "template"
        s["detail"] = reasoner.describe(s)
    log(f"  理由: {n_llm}/{len(top)} 由大模型生成, {len(top)-n_llm} 用特征模板兜底")
    ckpt_save("reason_done", {"llm": n_llm, "template": len(top) - n_llm})

# ============ 主流程 ============
def run(force_train=False):
    t0 = time.time()
    log("=" * 60)
    log("A股次日涨停(打板)预测 启动")
    # 滚动反馈: 1日预测/2日回测 —— 先标注已到期的历史预测池, 累积样本与战绩
    try:
        n_new = feedback.process_pending()
        if n_new:
            log(f"🔁 滚动回测完成: 新标注 {n_new} 个预测池, 样本库累计 {feedback.sample_count()} 条")
    except Exception as e:
        log(f"⚠️ 滚动回测异常(不影响本次预测): {e}")
    m, bt = ensure_model(force_train=force_train)
    snap, cands, zt = build_candidates()
    try:
        feedback.save_prices(snap)   # 记录本次预测时刻价, 供次日收益结算
    except Exception as e:
        log(f"⚠️ 预测时刻价落盘失败: {e}")
    news_items = data.fetch_news(pages=3, per_page=50)
    log(f"  资讯: {len(news_items)} 条 (新浪7x24)")
    indices = data.fetch_indices()
    market_summary = " | ".join(f"{x['name']} {x['pct']:+.2f}%" for x in indices if x.get("pct") is not None)
    scored = score_candidates(cands, news_items, snap=snap, indices=indices)
    if not scored:
        log("⚠️ 无有效候选, 退出")
        return None
    # 用户口径(2026-10-09): 只推荐预测时刻**仍可买入**的标的——已封涨停者挂单买不进,
    # 不予推荐; 每期推荐总数不超过 RECOMMEND_N(10) 只。
    # 落池同样只用可买池, 否则收益统计会把买不进的标的算进去, 虚高失真。
    buyable = [s for s in scored if not s.get("sealed")]
    n_sealed = len(scored) - len(buyable)
    log(f"  可买筛选: 剔除预测时刻已封板(买不进) {n_sealed} 只, 可买 {len(buyable)} 只 "
        f"(推荐上限 {RECOMMEND_N})")
    try:
        feedback.save_pool(buyable)   # 落盘(可买)预测池, 供次日运行时标注回测
    except Exception as e:
        log(f"⚠️ 预测池落盘失败: {e}")
    top = buyable[:RECOMMEND_N]
    attach_reasons(top)
    result = {
        # 统一用北京时间(UTC+8): 云端 runner 为 UTC 时区, 直接用 time.strftime 会显示 UTC 时间
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600)),
        "data_time": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600)),
        "market_summary": market_summary,
        "market_factors": dict(MARKET_SNAP),
        "sent_summary": {"n_news": len(news_items),
                         "n_stocks": sum(1 for s in scored if (s.get("sent") or {}).get("heat"))},
        "indices": indices,
        "n_universe": len(snap), "n_limit_up_today": len(zt), "n_candidates": len(cands),
        "n_scored": len(scored), "n_sealed_filtered": n_sealed, "n_recommend": len(top),
        "recommend_cap": RECOMMEND_N,
        "n_samples": m.get("n_samples"), "n_pos": m.get("n_pos"),
        "base_rate": m.get("base_rate", 0), "model_auc": m.get("auc"),
        "backtest": bt or {},
        "track_record": feedback.track_summary(10),
        "ret_stats": feedback.ret_stats(10),
        "top": [{"code": s["code"], "name": s["name"], "prob": round(s["prob"], 4),
                 "price": s.get("price"),
                 "today_pct": s.get("today_pct"), "industry": s.get("industry"),
                 "reason": s["reason"], "reason_source": s["reason_source"],
                 "detail": s["detail"], "news_n": s.get("news", {}).get("n", 0),
                 "sent": s.get("sent") or {}}
                for s in top],
    }
    md_path, js_path = report.save(result)
    log(f"✅ 完成, 用时 {time.time()-t0:.0f}s")
    log(f"   报告: {md_path}")
    ckpt_save("done", {"md": md_path, "elapsed": round(time.time() - t0, 1)})
    return result

def resume():
    """从检查点读取状态, 打印断点信息(供新会话继续)。"""
    if not os.path.exists(CKPT):
        log("无检查点, 从头运行 run()")
        return run()
    with open(CKPT, "r", encoding="utf-8") as fh:
        c = json.load(fh)
    log(f"检测到检查点: 阶段={c.get('phase')}, 更新={c.get('updated_at')}")
    return c

if __name__ == "__main__":
    force = "--train" in sys.argv or "--force" in sys.argv
    result = run(force_train=force)
    if result:
        print("\n==== TOP 预览 ====")
        for i, s in enumerate(result["top"][:10], 1):
            print(f"{i:>2}. {s['code']} {s['name']:<8} {s['prob']:.1%}  {s['reason']}")
