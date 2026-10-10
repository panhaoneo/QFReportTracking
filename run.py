#!/usr/bin/env python3
"""每日财报跟踪一键入口（详见 README）。

用法：
  python run.py --date 2025-10-09 --codes 688099,600176,300373
  python run.py --date 2025-10-09 --codes 688099 --quarter Q3 --year 2025
  python run.py --date 2025-10-09 --codes 688099 --offline        # 复用 raw 缓存（演示/无网）
  python run.py --date 2025-10-09 --codes 688099 --industry "688099=端侧AI" --git-commit

流程：采集 -> 指标计算 -> 人工 override 合并 -> 增长原因 -> 增量写 Markdown -> 站点数据 -> （可选）git commit
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime

from scripts import config as C
from scripts.calc_metrics import compute_metrics
from scripts import fetch_data
from scripts.generate_md import update_report_md
from scripts.merge_manual import apply_overrides, load_overrides
from scripts.summarize import generate_growth_reason
from scripts.update_site import main as update_site_main
from scripts.utils import get_logger, load_json, save_json

logger = get_logger()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="每日财报记录：采集/计算/归档/生成站点数据")
    p.add_argument("--date", required=True, help="运行日期 YYYY-MM-DD（通常是财报披露日）")
    p.add_argument("--codes", required=True, help="当日发布财报的股票代码，逗号分隔")
    p.add_argument("--quarter", default=None, help="报告期季度 Q1/Q2/Q3/Q4（默认按日期推断）")
    p.add_argument("--year", type=int, default=None, help="报告期年份（默认按日期推断）")
    p.add_argument("--industry", default="", help='行业兜底映射，如 "688099=端侧AI,600176=玻纤"')
    p.add_argument("--offline", action="store_true", help="离线模式：仅复用 raw/ 缓存，不联网抓取")
    p.add_argument("--no-llm", action="store_true", help="禁用 LLM 总结，仅用启发式模板")
    p.add_argument("--git-commit", action="store_true", help="生成后自动 git add + commit（不 push）")
    return p.parse_args(argv)


def _parse_kv(text: str) -> dict[str, str]:
    out = {}
    for seg in text.split(","):
        if "=" in seg:
            k, v = seg.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _git_commit(date_str: str, codes: list[str]) -> None:
    try:
        subprocess.run(["git", "add", "reports", "raw", "manual", "docs"], check=True, cwd=C.ROOT)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=C.ROOT)
        if diff.returncode == 0:
            logger.info("无变更，跳过 git commit")
            return
        msg = f"auto: {date_str} 财报记录（{len(codes)} 只）"
        subprocess.run(["git", "commit", "-m", msg], check=True, cwd=C.ROOT)
        logger.info("已提交: %s（未 push，请检查后手动 git push）", msg)
    except subprocess.CalledProcessError as exc:
        logger.warning("git 提交失败: %s", exc)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        logger.error("--codes 为空")
        return 2
    year, quarter = C.infer_period(args.date, args.quarter, args.year)
    industry_hints = _parse_kv(args.industry)
    logger.info("报告期: %s %s | 日期: %s | 股票: %s", year, quarter, args.date, ",".join(codes))

    raw_path = C.raw_json_path(year, quarter, args.date)
    prev_raw = load_json(raw_path) or {}
    prev_stocks: dict = prev_raw.get("stocks") or {}
    prev_hashes = {code: s.get("auto_hash") for code, s in prev_stocks.items() if s.get("auto_hash")}

    # 1) 采集（单只失败跳过，不影响其他）
    failures: list[str] = []
    fetched: dict[str, dict] = {}
    for code in codes:
        try:
            if args.offline:
                rec = fetch_data.fetch_offline(code, prev_raw)
                if rec is None:
                    raise fetch_data.FetchError("offline 模式但 raw 缓存中无该股票")
            else:
                rec = fetch_data.fetch_stock(code, year, quarter, args.date, industry_hints.get(code))
            fetched[code] = rec
            logger.info("[%s] %s 采集完成%s", code, rec.get("name") or "?", "（离线缓存）" if args.offline else "")
        except Exception as exc:
            failures.append(code)
            logger.error("[%s] 采集失败，已跳过: %s", code, exc)

    if not fetched:
        logger.error("全部股票采集失败，未生成任何记录")
        return 1

    # 2) 合并当天已存在的其他股票（保证 raw 累积、Markdown 行不丢）
    stocks: dict[str, dict] = {code: dict(rec) for code, rec in prev_stocks.items()}
    stocks.update(fetched)

    # 3) 逐条：指标计算 -> 人工 override -> 增长原因
    overrides = load_overrides(year, quarter, args.date)
    if overrides:
        logger.info("检测到人工 override: %s", ", ".join(sorted(overrides)))
    records: list[dict] = []
    manual_fields_map: dict[str, set[str]] = {}
    for code, rec in stocks.items():
        compute_metrics(rec)
        manual = apply_overrides(rec, overrides.get(code) or {})
        manual_fields_map[code] = manual
        if not rec.get("growth_reason"):
            rec["growth_reason"] = generate_growth_reason(rec, use_llm=not args.no_llm)
        records.append(rec)

    # 4) 增量生成 Markdown
    result = update_report_md(
        year, quarter, args.date, records,
        manual_fields_map=manual_fields_map, prev_hashes=prev_hashes,
    )
    logger.info(
        "报告: %s | 新建:%s 追加:%s 更新:%s 人工保护:%s 未变:%s",
        result["path"], int(result["created"]),
        result["added"] or "无", result["updated"] or "无",
        result["kept_manual"] or "无", result["unchanged"] or "无",
    )

    # 5) 持久化 raw（含行哈希 + 人工修改保护信息）
    new_raw = {
        "date": args.date,
        "year": year,
        "quarter": quarter,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stocks": {
            code: {**rec, "auto_hash": result["hashes"].get(code, rec.get("auto_hash"))}
            for code, rec in stocks.items()
        },
        "failed": failures,
    }
    save_json(raw_path, new_raw)

    # 6) 站点数据
    update_site_main()

    # 7) 待核验提示
    no_forecast = [str(r["code"]) for r in records if not (r.get("forecast") or {}).get("np_median_yi")]
    logger.info("完成。请核验 Markdown 中带 * 的字段；无机构预期的股票: %s", ", ".join(no_forecast) or "无")
    if failures:
        logger.warning("本次跳过的股票: %s", ", ".join(failures))

    if args.git_commit:
        _git_commit(args.date, codes)
    else:
        logger.info("下一步：检查 %s，必要时修改 manual 后重跑；提交建议 `git add -A && git commit ...`", result["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
