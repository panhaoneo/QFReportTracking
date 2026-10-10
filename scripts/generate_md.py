"""Markdown 报告增量生成。

保护规则：
1. 表格中已存在的行永不覆盖——除非该行哈希与 raw/*.json 中记录的 auto_hash 一致
   （说明它仍是上次脚本生成的原样内容，未被人工手改），此时允许用新数据重刷。
2. 哈希不一致或找不到记录的行 → 视为人工修改，永久保留、不覆盖。
3. 新股票追加到表格末尾；仅当文件缺失或没有表格时新建表格区块。
"""
from __future__ import annotations

import re
from datetime import datetime

from . import config as C
from .utils import (
    atomic_write_text,
    fmt_mcap,
    fmt_pct,
    fmt_price,
    fmt_ratio,
    md_cell_text,
    sha256_text,
)

ROW_RE = re.compile(r"^\|\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*\|")
SEP_RE = re.compile(r"^\|\s*:?-{2,}")
STATS_RE = re.compile(r"<!-- auto:stats -->(.*?)<!-- /auto:stats -->", re.S)

TABLE_SEP = "|" + "|".join(["---"] * len(C.TABLE_COLUMNS)) + "|"


def table_header(quarter: str) -> str:
    return "| " + " | ".join(h for _, h in C.table_columns(quarter)) + " |"


def _cell_code(line: str) -> str | None:
    if SEP_RE.match(line):
        return None
    m = ROW_RE.match(line)
    return m.group(1) if m else None


def render_row(rec: dict, manual_fields: set[str] | None = None) -> str:
    """渲染一行；manual_fields 中的字段由人工提供，不再加 * 标记。"""
    mf = manual_fields or set()

    def starred(field: str, text: str) -> str:
        return text if field in mf else f"{text}*"

    def text_override(field: str) -> str | None:
        """人工文本覆盖（如 yoy_pct="扭亏为盈"），优先生效。"""
        text = rec.get(f"{field}_text")
        return md_cell_text(text) if text else None

    dyn_pe = rec.get("dynamic_pe")
    forecast = rec.get("forecast") or {}
    has_np_forecast = forecast.get("np_median_yi") is not None

    dyn_pe_txt = text_override("dynamic_pe")
    if dyn_pe_txt is None:
        if dyn_pe is None:
            dyn_pe_txt = C.NO_FORECAST_TEXT if not has_np_forecast else "—"
        else:
            dyn_pe_txt = starred("dynamic_pe", fmt_ratio(dyn_pe))

    peg = rec.get("peg")
    peg_txt = text_override("peg")
    if peg_txt is None:
        if peg is None:
            peg_txt = C.NO_FORECAST_TEXT if (dyn_pe is None and not has_np_forecast) else "—"
        else:
            peg_txt = starred("peg", fmt_ratio(peg))

    reason = rec.get("growth_reason") or ""
    reason_txt = starred("growth_reason", md_cell_text(reason)) if reason else "—"

    note = rec.get("manual_note") or ""
    inst = rec.get("institution_expectation")
    if inst:
        note = f"{note}；机构预期：{inst}" if note else f"机构预期：{inst}"

    def plain(field: str, formatted: str) -> str:
        return text_override(field) or formatted

    cells = {
        "code": str(rec.get("code", "")).strip(),
        "name": md_cell_text(rec.get("name")),
        "price": plain("price", fmt_price(rec.get("price"))),
        "change_pct": plain("change_pct", fmt_pct(rec.get("change_pct"), digits=2)),
        "market_cap_yi": plain("market_cap_yi", fmt_mcap(rec.get("market_cap_yi"))),
        "industry": md_cell_text(rec.get("industry")),
        "dynamic_pe": dyn_pe_txt,
        "cum_pe": plain("cum_pe", fmt_ratio(rec.get("cum_pe"))),
        "yoy_pct": plain("yoy_pct", fmt_pct(rec.get("yoy_pct"))),
        "qoq_pct": plain("qoq_pct", fmt_pct(rec.get("qoq_pct"))),
        "peg": peg_txt,
        "growth_reason": reason_txt,
        "manual_note": md_cell_text(note),
    }
    return "| " + " | ".join(cells[key] for key, _ in C.TABLE_COLUMNS) + " |"


def _header_block(year: int, quarter: str, date_str: str) -> list[str]:
    return [
        f"# {year} {quarter} 财报跟踪 · {date_str}",
        "",
        "> 数据来源：AKShare/东财（行情、财务）、机构研报聚合（预期）、脚本自动归纳（增长原因，待核验）",
        "> `*` = 脚本自动生成、待人工核验；人工 override 的字段不带 `*`。",
        f"> 手动修改本文件的行会被永久保留，重跑只追加新股票；推荐改 `manual/{year}/{quarter}/{date_str}.json` 做字段级覆盖。",
        "",
    ]


def _stats_block(count: int, timestamp: str) -> list[str]:
    return [
        "<!-- auto:stats -->",
        f"共 {count} 只 · 脚本更新于 {timestamp}",
        "<!-- /auto:stats -->",
    ]


def _find_table(lines: list[str]) -> tuple[int, int] | None:
    """返回 (表头行idx, 表格最后一行idx)；找不到返回 None。"""
    header_idx = None
    for i, line in enumerate(lines):
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 1 and cells[1].strip() == "代码":
            header_idx = i
            break
    if header_idx is None:
        return None
    last = header_idx
    for j in range(header_idx + 1, len(lines)):
        if lines[j].startswith("|") and (SEP_RE.match(lines[j]) or ROW_RE.match(lines[j])):
            last = j
        else:
            break
    return header_idx, last


def update_report_md(
    year: int,
    quarter: str,
    date_str: str,
    records: list[dict],
    manual_fields_map: dict[str, set[str]] | None = None,
    prev_hashes: dict[str, str] | None = None,
) -> dict:
    """增量更新当日报告，返回 {path, created, added, updated, kept_manual, unchanged, hashes}。"""
    manual_fields_map = manual_fields_map or {}
    prev_hashes = prev_hashes or {}
    path = C.report_md_path(year, quarter, date_str)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    result = {
        "path": str(path),
        "created": False,
        "added": [],
        "updated": [],
        "kept_manual": [],
        "unchanged": [],
        "hashes": dict(prev_hashes),
    }

    if not path.exists():
        rows = [render_row(rec, manual_fields_map.get(str(rec.get("code")))) for rec in records]
        for rec, row in zip(records, rows):
            code = str(rec.get("code"))
            result["hashes"][code] = sha256_text(row)
            result["added"].append(code)
        lines = _header_block(year, quarter, date_str) + [table_header(quarter), TABLE_SEP] + rows
        lines += [""] + _stats_block(len(rows), timestamp)
        atomic_write_text(path, "\n".join(lines) + "\n")
        result["created"] = True
        return result

    lines = path.read_text(encoding="utf-8").splitlines()
    table = _find_table(lines)
    existing: dict[str, int] = {}
    if table:
        for i in range(table[0] + 1, table[1] + 1):
            code = _cell_code(lines[i])
            if code:
                existing[code] = i

    new_lines = list(lines)
    to_append: list[str] = []
    rows_changed = False

    for rec in records:
        code = str(rec.get("code"))
        row = render_row(rec, manual_fields_map.get(code))
        if code in existing:
            idx = existing[code]
            old_row = new_lines[idx]
            prev_hash = prev_hashes.get(code)
            is_machine_owned = prev_hash is not None and sha256_text(old_row) == prev_hash
            if is_machine_owned:
                if old_row != row:
                    new_lines[idx] = row
                    result["hashes"][code] = sha256_text(row)
                    result["updated"].append(code)
                    rows_changed = True
                else:
                    result["unchanged"].append(code)
            else:
                result["kept_manual"].append(code)
            continue
        if row not in to_append:
            to_append.append(row)
            result["hashes"][code] = sha256_text(row)
            result["added"].append(code)

    if to_append:
        if table:
            insert_at = table[1] + 1
            new_lines[insert_at:insert_at] = to_append
        else:
            new_lines += ["", f"## {date_str} 追加更新", "", table_header(quarter), TABLE_SEP] + to_append
        rows_changed = True

    # 行有变化时才刷新统计脚注，避免仅时间戳变化产生无意义 diff
    if rows_changed:
        count = sum(1 for line in new_lines if _cell_code(line))
        stats = _stats_block(count, timestamp)
        text = "\n".join(new_lines)
        if STATS_RE.search(text):
            new_lines = STATS_RE.sub(lambda _: "\n".join(stats), text).splitlines()
        else:
            new_lines = new_lines + [""] + stats

    new_text = "\n".join(new_lines) + "\n"
    if new_text != "\n".join(lines) + "\n":
        atomic_write_text(path, new_text)
    # 人工修改行在 result["hashes"] 中保留旧哈希（初值即 prev_hashes），
    # 下次运行仍能与旧值不匹配，从而继续受保护。
    return result
