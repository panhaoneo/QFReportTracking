"""站点数据生成：解析 reports/ 下的 Markdown，产出 VitePress 所需数据。

- docs/public/data/reports.json     看板筛选用的全量数据（按天分组 + 行业/季度索引）
- docs/reports/YYYY/Qn/YYYY-MM-DD.md 报告原文拷贝（带 frontmatter，供站点浏览）
- docs/.vitepress/reports-sidebar.json 侧边栏数据

直接解析 Markdown 而非 raw JSON，保证"人工直接改 md"也会同步到站点。
本脚本仅依赖标准库，可在 CI 中独立运行：python3 scripts/update_site.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from . import config as C
    from .utils import atomic_write_text, get_logger, parse_cell
except ImportError:  # 允许 python3 scripts/update_site.py 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts import config as C
    from scripts.utils import atomic_write_text, get_logger, parse_cell

logger = get_logger()

_PATH_RE = re.compile(r"(\d{4})/(Q[1-4])/(\d{4}-\d{2}-\d{2})\.md$")
STAR_FIELDS = ("dynamic_pe", "peg", "growth_reason")


def _split_row(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [c.strip() for c in body.split("|")]


def parse_report_md(path: Path) -> dict | None:
    m = _PATH_RE.search(path.as_posix())
    if not m:
        return None
    year, quarter, date_str = m.group(1), m.group(2), m.group(3)

    lines = path.read_text(encoding="utf-8").splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines)
         if line.startswith("|") and _split_row(line)[0] == "代码"),
        None,
    )
    if header_idx is None:
        return None

    headers = _split_row(lines[header_idx])
    col_map: dict[str, int] = {}
    for idx, head in enumerate(headers):
        field = C.field_for_header(head)
        if field and field not in col_map:
            col_map[field] = idx
    rows = []
    for line in lines[header_idx + 1:]:
        if not line.startswith("|"):
            break
        cells = _split_row(line)
        if not cells or cells[0].startswith("---") or cells[0] == "代码":
            continue
        if len(cells) != len(headers):
            logger.warning("%s 行格式异常已跳过: %s", path, line[:60])
            continue
        row: dict = {"year": int(year), "quarter": quarter, "date": date_str}
        stars: list[str] = []
        cells_raw: dict[str, str] = {}
        for field, _ in C.TABLE_COLUMNS:
            idx = col_map.get(field)
            raw = cells[idx] if idx is not None and idx < len(cells) else "—"
            cells_raw[field] = raw
            if field == "code":
                row[field] = raw.replace("*", "").strip()
                continue
            if field == "name":
                row[field] = raw
                continue
            if raw.endswith("*"):
                stars.append(field)
            row[field] = parse_cell(raw)
        row["star_fields"] = stars
        row["cells"] = cells_raw
        rows.append(row)
    return {"date": date_str, "year": int(year), "quarter": quarter, "rows": rows}


def build_site_data() -> dict:
    days: list[dict] = []
    if C.REPORTS_DIR.exists():
        md_files = sorted(C.REPORTS_DIR.rglob("*.md"))
        for md in md_files:
            parsed = parse_report_md(md)
            if parsed:
                days.append(parsed)
    days.sort(key=lambda d: (d["year"], d["quarter"], d["date"]), reverse=True)

    industries = sorted({r["industry"] for d in days for r in d["rows"] if isinstance(r.get("industry"), str)})
    quarters = sorted({f"{d['year']}/{d['quarter']}" for d in days}, reverse=True)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "industries": industries,
        "quarters": quarters,
        "total_rows": sum(len(d["rows"]) for d in days),
    }


def _wrap_tables(content: str) -> str:
    """给 Markdown 表格包一层 .rpt-table-wrap（站点侧用于横向滚动与样式控制）。

    markdown-it 会正确解析 HTML 块之间的表格；源文件 reports/*.md 保持纯 Markdown 不变。
    """
    lines = content.splitlines()
    out: list[str] = []
    in_table = False
    for line in lines:
        is_row = line.lstrip().startswith("|")
        if is_row and not in_table:
            out += ['<div class="rpt-table-wrap">', ""]
            in_table = True
        elif not is_row and in_table:
            out += ["", "</div>"]
            in_table = False
        out.append(line)
    if in_table:
        out += ["", "</div>"]
    return "\n".join(out) + "\n"


def sync_docs(days: list[dict]) -> None:
    """拷贝报告原文到 docs/reports 并生成侧边栏（供 config.mts 读取）。"""
    groups: dict[str, list[dict]] = {}
    for day in days:
        src = C.report_md_path(day["year"], day["quarter"], day["date"])
        if not src.exists():
            continue
        dst = C.DOCS_REPORTS_DIR / str(day["year"]) / day["quarter"] / f"{day['date']}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = _wrap_tables(src.read_text(encoding="utf-8"))
        title = f"{day['date']} 财报跟踪"
        frontmatter = f'---\ntitle: "{title}"\naside: false\n---\n\n'
        atomic_write_text(dst, frontmatter + content)
        key = f"{day['year']} / {day['quarter']}"
        groups.setdefault(key, []).append(
            {"text": day["date"], "link": f"/reports/{day['year']}/{day['quarter']}/{day['date']}"}
        )
    sidebar = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "groups": [
            {"text": key, "items": sorted(items, key=lambda x: x["text"], reverse=True)}
            for key, items in sorted(groups.items(), reverse=True)
        ],
    }
    save_path = C.DOCS_SIDEBAR_FILE
    save_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(save_path, json.dumps(sidebar, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    data = build_site_data()
    C.DOCS_PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = C.DOCS_PUBLIC_DATA_DIR / "reports.json"
    atomic_write_text(out, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sync_docs(data["days"])
    logger.info(
        "站点数据已生成: %s（%d 天 / %d 条记录）", out.relative_to(C.ROOT), len(data["days"]), data["total_rows"]
    )


if __name__ == "__main__":
    main()
