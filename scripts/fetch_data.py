"""数据采集层。

数据源优先级：akshare（默认）> tushare（配置 TUSHARE_TOKEN 时作为行情兜底）> offline（复用 raw 缓存）。
每个维度独立容错：单项失败写入 rec["errors"]，不影响其他维度；整只股票关键字段全失败时抛 FetchError，
由 run.py 记录日志并跳过，不影响其他股票。

输出 record（扁平结构，与表格字段对齐 + 明细附加项）：
{
  "code","name","price","change_pct","market_cap_yi","industry",
  "q_deduct_yi","cum_deduct_yi","prev_year_q_deduct_yi","prev_q_deduct_yi",
  "forecast": {"np_median_yi","growth_median_pct","n_reports","target_year","source"},
  "news": [...], "announcements": [...],
  "sources": {...}, "errors": [...], "fetched_at": "..."
}
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from . import config as C
from .utils import get_logger, safe_float

logger = get_logger()

_QUARTER_END_DASH = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}

# 上一次季度的 (year_delta, quarter)
_PREV_QUARTER = {"Q1": (-1, "Q4"), "Q2": (0, "Q1"), "Q3": (0, "Q2"), "Q4": (0, "Q3")}


class FetchError(Exception):
    """单只股票关键数据全部获取失败。"""


def _import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError as exc:  # 提示安装
        raise FetchError("未安装 akshare，请先 pip install -r requirements.txt") from exc


_YI_FACTORS = (("万亿", 1e4), ("万元", 1e-4), ("万", 1e-4), ("亿元", 1.0), ("亿", 1.0))


def _to_yi(value) -> float | None:
    """统一为亿元：支持 '1.75亿' / '1234万' / 纯数字（>1e6 视为元，自动 /1e8）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v != v:  # NaN
            return None
        return round(v / 1e8, 4) if abs(v) > 1e6 else round(v, 4)
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "--", "—", "None", "nan", "NaN", "false", "False"):
        return None
    for unit, factor in _YI_FACTORS:
        if s.endswith(unit):
            v = safe_float(s[: -len(unit)])
            return None if v is None else round(v * factor, 4)
    v = safe_float(s)
    if v is None:
        return None
    return round(v / 1e8, 4) if abs(v) > 1e6 else round(v, 4)


def _find_col(df, *keywords):
    for col in df.columns:
        name = str(col)
        if all(k in name for k in keywords):
            return col
    return None


# ---------------- 行情 ----------------

_spot_df = None


def _spot_table(ak):
    global _spot_df
    if _spot_df is None:
        _spot_df = ak.stock_zh_a_spot_em()
    return _spot_df


def _market_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return "bj"


def _fetch_quote_tencent(code: str) -> dict:
    """腾讯行情（单只一条请求，含价格/涨跌幅/总市值），字段 45=总市值(亿元)。"""
    try:
        import requests

        r = requests.get(f"https://qt.gtimg.cn/q={_market_prefix(code)}{code}", timeout=10)
        r.encoding = "gbk"
        parts = r.text.split('"')
        if len(parts) < 2:
            return {}
        f = parts[1].split("~")
        if len(f) < 46:
            return {}
        name = f[1].strip()
        out: dict = {"sources_quote": "tencent:qt.gtimg.cn"}
        if name:
            out["name"] = name
        for key, raw in (("price", f[3]), ("change_pct", f[32]), ("market_cap_yi", f[45])):
            v = safe_float(raw)
            if v is not None:
                out[key] = v
        return out
    except Exception as exc:
        logger.warning("[%s] 腾讯行情失败: %s", code, exc)
        return {}


def _fetch_quote(ak, code: str) -> dict:
    """行情：价格/涨跌幅/总市值/名称/行业，多源兜底（腾讯 > 东财快照 > 东财个股信息 > 巨潮 > tushare）。"""
    out: dict = _fetch_quote_tencent(code)
    if out.get("price") is None or not out.get("name"):
        _fetch_quote_em(ak, code, out)
    if not out.get("industry"):
        _fetch_industry_em(ak, code, out)
    if not out.get("industry"):
        _fetch_industry_cninfo(ak, code, out)
    if out.get("price") is None:
        _tushare_quote_fallback(code, out)
    return out


def _fetch_industry_cninfo(ak, code: str, out: dict) -> None:
    try:
        df = ak.stock_profile_cninfo(symbol=code)
        if df is not None and len(df):
            row = df.iloc[0]
            industry = str(row.get("所属行业") or "").strip()
            if industry and industry not in ("nan", "None"):
                out["industry"] = industry
                out["sources_industry"] = "akshare:cninfo_profile"
    except Exception as exc:
        logger.warning("[%s] 巨潮公司概况（行业）失败: %s", code, exc)


def _fetch_quote_em(ak, code: str, out: dict) -> None:
    """东财全市场快照兜底（仅在腾讯缺数据时调用；只填充缺失字段）。"""
    try:
        df = _spot_table(ak)
        row = df[df["代码"] == code]
        if not len(row):
            return
        r = row.iloc[0]
        name = str(r.get("名称") or "").strip()
        if name and not out.get("name"):
            out["name"] = name
        for key, raw in (
            ("price", r.get("最新价")),
            ("change_pct", r.get("涨跌幅")),
            ("market_cap_yi", r.get("总市值")),
        ):
            if out.get(key) is not None:
                continue
            v = _to_yi(raw) if key == "market_cap_yi" else safe_float(raw)
            if v is not None:
                out[key] = v
        if "sources_quote" not in out:
            out["sources_quote"] = "akshare:eastmoney_spot"
    except Exception as exc:
        logger.warning("[%s] 东财行情快照失败: %s", code, exc)


def _fetch_industry_em(ak, code: str, out: dict) -> None:
    try:
        info = ak.stock_individual_info_em(symbol=code, timeout=15)
        kv = dict(zip(info["item"], info["value"]))
        name = str(kv.get("股票简称") or "").strip()
        if name and not out.get("name"):
            out["name"] = name
        mcap = _to_yi(kv.get("总市值"))
        if mcap is not None and out.get("market_cap_yi") is None:
            out["market_cap_yi"] = mcap
        if kv.get("行业"):
            out["industry"] = str(kv["行业"]).strip()
            out["sources_industry"] = "akshare:eastmoney_individual_info"
    except Exception as exc:
        logger.warning("[%s] 东财个股信息（行业）失败: %s", code, exc)


def _tushare_quote_fallback(code: str, out: dict) -> None:
    """可选：配置 TUSHARE_TOKEN 时用 tushare 兜底行情与行业。"""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return
    try:
        import tushare as ts

        pro = ts.pro_api(token)
        suffix = "SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith(("4", "8")) else "SZ")
        basic = pro.stock_basic(ts_code=f"{code}.{suffix}", fields="ts_code,name,industry")
        if len(basic):
            out.setdefault("name", str(basic.iloc[0]["name"]))
            if basic.iloc[0].get("industry"):
                out.setdefault("industry", str(basic.iloc[0]["industry"]))
        daily = pro.daily_basic(ts_code=f"{code}.{suffix}", fields="ts_code,close,total_mv")
        if len(daily):
            out.setdefault("price", safe_float(daily.iloc[0]["close"]))
            out.setdefault("market_cap_yi", _to_yi(daily.iloc[0]["total_mv"]))
        out["sources_quote"] = "tushare"
    except Exception as exc:
        logger.warning("[%s] tushare 兜底失败: %s", code, exc)


# ---------------- 财务（扣非净利润，单季/累计） ----------------

def _fetch_financials_ths(ak, code: str) -> dict | None:
    """同花顺「按单季度」财务摘要，可直接拿到单季扣非与同比/环比。"""
    df = ak.stock_financial_abstract_ths(symbol=code, indicator="按单季度")
    if df is None or df.empty:
        return None
    period_col = _find_col(df, "报告期") or df.columns[0]
    deduct_col = _find_col(df, "扣非", "净利")
    yoy_col = _find_col(df, "扣非", "同比")
    qoq_col = _find_col(df, "扣非", "环比")
    if deduct_col is None:
        return None
    series = {}
    for _, row in df.iterrows():
        p = str(row[period_col]).strip()[:10]
        v = _to_yi(row[deduct_col])
        if v is not None:
            series[p] = v
    return {"series": series, "yoy_col": yoy_col, "qoq_col": qoq_col, "df": df, "period_col": period_col}


def _fetch_financials_sina(ak, code: str, start_year: int) -> dict | None:
    """新浪财务指标（累计口径），单季值由累计差分得到。"""
    df = ak.stock_financial_analysis_indicator(symbol=code, start_year=str(start_year))
    if df is None or df.empty:
        return None
    date_col = _find_col(df, "日期") or df.columns[0]
    deduct_col = _find_col(df, "扣除非经常性损益后的净利润") or _find_col(df, "扣非")
    if deduct_col is None:
        return None
    cumulative = {}
    for _, row in df.iterrows():
        d = str(row[date_col]).strip()[:10]
        v = _to_yi(row[deduct_col])
        if v is not None:
            cumulative[d] = v
    return {"cumulative": cumulative}


def _cumulative_to_single(cumulative: dict[str, float]) -> dict[str, float]:
    """累计值 -> 单季值：Q2=H1-Q1，Q3=前三季-H1，Q4=全年-前三季，Q1=累计本身。"""
    single: dict[str, float] = {}
    for d, v in cumulative.items():
        mmdd = d[5:10]
        if mmdd in ("03-31", "06-30", "09-30", "12-31"):
            if mmdd == "03-31":
                single[d] = v
                continue
            prev_end = {"06-30": "03-31", "09-30": "06-30", "12-31": "09-30"}[mmdd]
            prev = cumulative.get(f"{d[:4]}-{prev_end}")
            if prev is not None:
                single[d] = round(v - prev, 4)
    return single


def _pick_quarter_values(series: dict[str, float], year: int, quarter: str) -> dict:
    """从单季序列中取当季、去年同期、上一季度三个值。"""
    cur_end = f"{year}-{_QUARTER_END_DASH[quarter]}"
    dy, dq = _PREV_QUARTER[quarter]
    prev_q_end = f"{year + dy}-{_QUARTER_END_DASH[dq]}"
    prev_y_end = f"{year - 1}-{_QUARTER_END_DASH[quarter]}"
    return {
        "q_deduct_yi": series.get(cur_end),
        "prev_year_q_deduct_yi": series.get(prev_y_end),
        "prev_q_deduct_yi": series.get(prev_q_end),
    }


def _fetch_financials(ak, code: str, year: int, quarter: str) -> dict:
    errors = []
    try:
        ths = _fetch_financials_ths(ak, code)
        if ths:
            vals = _pick_quarter_values(ths["series"], year, quarter)
            if vals["q_deduct_yi"] is not None:
                cur_end = f"{year}-{_QUARTER_END_DASH[quarter]}"
                cur_row = ths["df"][ths["df"][ths["period_col"]].astype(str).str.startswith(cur_end)]
                if len(cur_row):
                    if ths.get("yoy_col") is not None:
                        vals["yoy_direct"] = safe_float(cur_row.iloc[0][ths["yoy_col"]])
                    if ths.get("qoq_col") is not None:
                        vals["qoq_direct"] = safe_float(cur_row.iloc[0][ths["qoq_col"]])
                vals["sources_fin"] = "akshare:ths_quarterly"
                return vals
    except Exception as exc:
        errors.append(f"ths_financials: {exc}")
        logger.warning("[%s] 同花顺单季财务失败: %s", code, exc)

    try:
        sina = _fetch_financials_sina(ak, code, start_year=year - 2)
        if sina:
            single = _cumulative_to_single(sina["cumulative"])
            vals = _pick_quarter_values(single, year, quarter)
            vals["cum_deduct_yi"] = sina["cumulative"].get(f"{year}-{_QUARTER_END_DASH[quarter]}")
            if vals["q_deduct_yi"] is not None:
                vals["sources_fin"] = "akshare:sina_indicator"
                return vals
    except Exception as exc:
        errors.append(f"sina_financials: {exc}")
        logger.warning("[%s] 新浪财务指标失败: %s", code, exc)

    return {"errors": errors}


def _attach_cumulative(vals: dict, ak, code: str, year: int, quarter: str) -> None:
    """确保累计扣非（前三季度累计）存在；THS 单季口径下由单季累加。"""
    if vals.get("cum_deduct_yi") is not None:
        return
    try:
        sina = _fetch_financials_sina(ak, code, start_year=year - 1)
        if sina:
            cum = sina["cumulative"].get(f"{year}-{_QUARTER_END_DASH[quarter]}")
            if cum is not None:
                vals["cum_deduct_yi"] = cum
                return
    except Exception:
        pass
    try:
        ths = _fetch_financials_ths(ak, code)
    except Exception:
        ths = None
    if ths:
        order = [f"{year}-{_QUARTER_END_DASH[q]}" for q in ("Q1", "Q2", "Q3", "Q4")]
        upto = ("Q1", "Q2", "Q3", "Q4").index(quarter) + 1
        parts = [ths["series"].get(d) for d in order[:upto]]
        if parts and all(p is not None for p in parts):
            vals["cum_deduct_yi"] = round(sum(parts), 4)


# ---------------- 机构预期 ----------------

def _fetch_forecast(ak, code: str, year: int) -> dict | None:
    """机构盈利预测：优先同花顺预测，其次东财盈利预测表。返回 None 表示无预期数据。"""
    try:
        df = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报净利润")
        if df is not None and not df.empty:
            row = df[df["年度"].astype(str).str.contains(str(year))]
            if len(row):
                r = row.iloc[0]
                np_mean = _to_yi(r.get("均值"))
                if np_mean is not None:
                    n = safe_float(r.get("预测机构数"))
                    return {
                        "np_median_yi": np_mean,
                        "n_reports": int(n) if n is not None else None,
                        "target_year": year,
                        "source": "akshare:ths_forecast_mean",
                        "note": "数据源提供机构均值，暂以均值近似中值",
                        "np_min_yi": _to_yi(r.get("最小值")),
                        "np_max_yi": _to_yi(r.get("最大值")),
                    }
    except Exception as exc:
        logger.warning("[%s] 同花顺盈利预测失败: %s", code, exc)

    try:
        df = ak.stock_profit_forecast_em(symbol=code)
        if df is not None and not df.empty:
            year_col = _find_col(df, str(year), "预测", "净利润")
            n_col = _find_col(df, "研报数") or _find_col(df, "机构")
            if year_col is not None:
                v = _to_yi(df.iloc[0][year_col])
                if v is not None:
                    n = safe_float(df.iloc[0][n_col]) if n_col is not None else None
                    return {
                        "np_median_yi": v,
                        "n_reports": int(n) if n is not None else None,
                        "target_year": year,
                        "source": "akshare:eastmoney_forecast",
                    }
    except Exception as exc:
        logger.warning("[%s] 东财盈利预测失败: %s", code, exc)
    return None


# ---------------- 新闻 / 公告 ----------------

def _fetch_news(ak, code: str, limit: int = 5) -> list[str]:
    try:
        df = ak.stock_news_em(symbol=code)
        col = _find_col(df, "新闻标题") or _find_col(df, "标题")
        if col is None:
            return []
        return [str(t).strip() for t in df[col].head(limit) if str(t).strip()]
    except Exception as exc:
        logger.warning("[%s] 个股新闻失败: %s", code, exc)
        return []


def _fetch_announcements(ak, code: str, date_str: str, limit: int = 5) -> list[str]:
    try:
        end = datetime.strptime(date_str, "%Y-%m-%d")
        start = end - timedelta(days=10)
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        col = _find_col(df, "公告标题") or _find_col(df, "标题")
        if col is None:
            return []
        return [str(t).strip() for t in df[col].head(limit) if str(t).strip()]
    except Exception as exc:
        logger.warning("[%s] 巨潮公告失败: %s", code, exc)
        return []


# ---------------- 对外入口 ----------------

def fetch_stock(code: str, year: int, quarter: str, date_str: str, industry_hint: str | None = None) -> dict:
    ak = _import_akshare()
    rec: dict = {
        "code": code,
        "report_period": quarter,
        "name": "",
        "price": None,
        "change_pct": None,
        "market_cap_yi": None,
        "industry": industry_hint or None,
        "q_deduct_yi": None,
        "cum_deduct_yi": None,
        "prev_year_q_deduct_yi": None,
        "prev_q_deduct_yi": None,
        "forecast": None,
        "news": [],
        "announcements": [],
        "sources": {},
        "errors": [],
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    quote = _fetch_quote(ak, code)
    for k in ("name", "price", "change_pct", "market_cap_yi", "industry"):
        if quote.get(k) is not None:
            rec[k] = quote[k]
    for k in ("sources_quote", "sources_industry"):
        if quote.get(k):
            rec["sources"][k.replace("sources_", "")] = quote[k]

    fin = _fetch_financials(ak, code, year, quarter)
    if fin.get("errors"):
        rec["errors"].extend(fin["errors"])
    _attach_cumulative(fin, ak, code, year, quarter)
    for k in ("q_deduct_yi", "cum_deduct_yi", "prev_year_q_deduct_yi", "prev_q_deduct_yi", "yoy_direct", "qoq_direct"):
        if fin.get(k) is not None:
            rec[k] = fin[k]
    if fin.get("sources_fin"):
        rec["sources"]["fin"] = fin["sources_fin"]

    rec["forecast"] = _fetch_forecast(ak, code, year)
    if rec["forecast"] is None:
        rec["errors"].append("forecast: 无机构预期数据（可人工 override np_median_yi）")
    rec["news"] = _fetch_news(ak, code)
    rec["announcements"] = _fetch_announcements(ak, code, date_str)

    if not rec["name"] and rec["price"] is None and rec["q_deduct_yi"] is None:
        raise FetchError(f"{code} 关键数据全部获取失败: {'; '.join(rec['errors']) or '未知错误'}")
    return rec


def fetch_offline(code: str, prev_raw: dict | None) -> dict | None:
    """离线模式：复用 raw 缓存中的记录。"""
    if not prev_raw:
        return None
    stock = (prev_raw.get("stocks") or {}).get(code)
    if not stock:
        return None
    rec = dict(stock)
    rec.pop("auto_hash", None)
    rec["offline"] = True
    return rec
