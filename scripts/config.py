"""全局配置：目录路径、字段定义、报告期推断。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPORTS_DIR = ROOT / "reports"
RAW_DIR = ROOT / "raw"
MANUAL_DIR = ROOT / "manual"
DOCS_DIR = ROOT / "docs"
DOCS_REPORTS_DIR = DOCS_DIR / "reports"
DOCS_PUBLIC_DATA_DIR = DOCS_DIR / "public" / "data"
DOCS_VITEPRESS_DIR = DOCS_DIR / ".vitepress"
DOCS_SIDEBAR_FILE = DOCS_VITEPRESS_DIR / "reports-sidebar.json"
LOGS_DIR = ROOT / "logs"

# ---- 报告期推断（A 股披露节奏）----
# 1-3月 -> 上一年 Q4（年报季）；4-6月 -> Q1（一季报/季报空窗）；
# 7-8月 -> Q2（中报季）；9-12月 -> Q3（三季报/空窗）
_MONTH_TO_QUARTER = {
    1: ("prev", "Q4"), 2: ("prev", "Q4"), 3: ("prev", "Q4"),
    4: ("cur", "Q1"), 5: ("cur", "Q1"), 6: ("cur", "Q1"),
    7: ("cur", "Q2"), 8: ("cur", "Q2"),
    9: ("cur", "Q3"), 10: ("cur", "Q3"), 11: ("cur", "Q3"), 12: ("cur", "Q3"),
}


def infer_period(date_str: str, quarter: str | None = None, year: int | None = None) -> tuple[int, str]:
    """根据日期推断 (财报年份, 季度)，--quarter/--year 显式传入时优先生效。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if quarter is None:
        base, quarter = _MONTH_TO_QUARTER[dt.month]
        year = dt.year - 1 if base == "prev" else dt.year
    quarter = quarter.upper()
    if quarter not in ("Q1", "Q2", "Q3", "Q4"):
        raise ValueError(f"非法季度: {quarter}")
    if year is None:
        year = dt.year
    return int(year), quarter


def period_dir(year: int, quarter: str) -> str:
    return f"{year}/{quarter}"


def report_md_path(year: int, quarter: str, date_str: str) -> Path:
    return REPORTS_DIR / str(year) / quarter / f"{date_str}.md"


def raw_json_path(year: int, quarter: str, date_str: str) -> Path:
    return RAW_DIR / str(year) / quarter / f"{date_str}.json"


def manual_json_path(year: int, quarter: str, date_str: str) -> Path:
    return MANUAL_DIR / str(year) / quarter / f"{date_str}.json"


# ---- Markdown 表格定义（顺序即列顺序）----
# 动态指标列名带季度前缀（Q3 时与需求 schema 完全一致），便于每季度复用
def table_columns(quarter: str | None = None) -> list[tuple[str, str]]:
    q = f"Q{str(quarter or 'Q3').lstrip('Q')}"
    return [
        ("code", "代码"),
        ("name", "股票"),
        ("price", "股价"),
        ("change_pct", "当日涨跌幅"),
        ("market_cap_yi", "总市值(亿)"),
        ("industry", "行业"),
        ("np_forecast_yi", "机构预期利润(亿)"),
        ("dynamic_pe", f"{q}动态PE(中值)"),
        ("cum_pe", f"累计{q}动态PE"),
        ("yoy_pct", f"{q}扣非净利润同比"),
        ("qoq_pct", f"{q}扣非净利润环比"),
        ("peg", "PEG"),
        ("links", "公告/研报链接"),
        ("remark", "备注"),
    ]


TABLE_COLUMNS: list[tuple[str, str]] = table_columns("Q3")


def field_for_header(header: str) -> str | None:
    """按表头文本反查字段名（兼容任意季度前缀与列顺序变化）。"""
    h = header.strip()
    exact = {
        "代码": "code",
        "股票": "name",
        "股价": "price",
        "当日涨跌幅": "change_pct",
        "总市值(亿)": "market_cap_yi",
        "总市值": "market_cap_yi",
        "行业": "industry",
        "机构预期利润(亿)": "np_forecast_yi",
        "机构预期利润": "np_forecast_yi",
        "PEG": "peg",
        "公告/研报链接": "links",
        "资料链接": "links",
        "备注": "remark",
        "人工备注": "remark",  # 历史文件的旧列名
    }
    if h in exact:
        return exact[h]
    if "动态PE" in h:
        return "dynamic_pe" if "中值" in h else "cum_pe"
    if "扣非净利润同比" in h:
        return "yoy_pct"
    if "扣非净利润环比" in h:
        return "qoq_pct"
    return None

# 自动生成、需人工核验的字段（渲染时加 * 标记）
STAR_FIELDS = {"np_forecast_yi", "dynamic_pe", "peg"}

# 机构预期缺失时 Q3动态PE/PEG 列的占位文案
NO_FORECAST_TEXT = "无预期*"

# manual/*.json 中 overrides 支持的字段（字段名 -> 中文说明，供 README 与校验使用）
OVERRIDABLE_FIELDS = {
    "price": "股价",
    "change_pct": "当日涨跌幅(%)",
    "market_cap_yi": "总市值(亿元)",
    "industry": "行业",
    "np_median_yi": "机构预测全年净利润中值(亿元)，会联动重算动态PE与PEG",
    "q_deduct_yi": "当季单季扣非净利润(亿元)，会联动重算同比/环比/PEG",
    "prev_year_q_deduct_yi": "去年同期单季扣非净利润(亿元)",
    "prev_q_deduct_yi": "上一季度单季扣非净利润(亿元)",
    "dynamic_pe": "Q3动态PE(中值)，直接覆盖（不改动np_median_yi）",
    "cum_pe": "累计Q3动态PE",
    "yoy_pct": "Q3扣非净利润同比(%)，负基数场景可填文本：\"扭亏为盈\"等，将原样展示且PEG置—",
    "qoq_pct": "Q3扣非净利润环比(%)，同样支持文本覆盖",
    "peg": "PEG（支持文本覆盖）",
    "links": "公告/研报链接（Markdown 链接文本，可人工修正整格内容）",
    "remark": "备注（仅人工填写，脚本不写入）",
}
