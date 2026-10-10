"""指标计算（口径与 README「指标公式」一致）：

- Q3动态PE(中值) = 总市值(亿) / 机构预测全年净利润中值(亿)
- 累计Q3动态PE   = 总市值(亿) / (前三季度累计扣非净利润(亿) × 4/3)
- Q3扣非净利润同比 = (Q3单季扣非 - 去年Q3单季扣非) / |去年Q3单季扣非| × 100
- Q3扣非净利润环比 = (Q3单季扣非 - Q2单季扣非) / |Q2单季扣非| × 100
- PEG = Q3动态PE(中值) / Q3扣非净利润同比(百分数数值)，同比<=0 或缺失时为 None
"""
from __future__ import annotations

from .utils import safe_float


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def dynamic_pe(market_cap_yi: float | None, np_median_yi: float | None) -> float | None:
    if not market_cap_yi or not np_median_yi or np_median_yi <= 0:
        return None
    return market_cap_yi / np_median_yi


def cum_pe(market_cap_yi: float | None, cum_deduct_yi: float | None) -> float | None:
    if not market_cap_yi or not cum_deduct_yi or cum_deduct_yi <= 0:
        return None
    annualized = cum_deduct_yi * 4.0 / 3.0
    return market_cap_yi / annualized


def peg(pe: float | None, yoy_pct: float | None) -> float | None:
    """注意：与常见 PEG=PE/(增速×100) 口径不同，此处沿用报表口径 PE/同比百分数。"""
    if pe is None or yoy_pct is None or yoy_pct <= 0:
        return None
    return pe / yoy_pct


def compute_metrics(rec: dict) -> dict:
    """根据 record 中的原始成分重算全部衍生指标（就地更新并返回）。"""
    market_cap = safe_float(rec.get("market_cap_yi"))
    forecast = rec.get("forecast") or {}
    np_median = safe_float(forecast.get("np_median_yi"))
    cum_deduct = safe_float(rec.get("cum_deduct_yi"))

    rec["yoy_pct"] = pct_change(safe_float(rec.get("q_deduct_yi")), safe_float(rec.get("prev_year_q_deduct_yi")))
    rec["qoq_pct"] = pct_change(safe_float(rec.get("q_deduct_yi")), safe_float(rec.get("prev_q_deduct_yi")))
    # 成分缺失时用数据源直接给出的同比/环比兜底
    if rec["yoy_pct"] is None:
        rec["yoy_pct"] = safe_float(rec.get("yoy_direct"))
    if rec["qoq_pct"] is None:
        rec["qoq_pct"] = safe_float(rec.get("qoq_direct"))
    rec["dynamic_pe"] = dynamic_pe(market_cap, np_median)
    rec["cum_pe"] = cum_pe(market_cap, cum_deduct)
    rec["peg"] = peg(rec["dynamic_pe"], rec["yoy_pct"])
    return rec


def recompute_peg(rec: dict) -> None:
    """在 yoy/pe 被人工覆盖后保持 PEG 联动（仅当 PEG 未被显式覆盖时调用）。"""
    rec["peg"] = peg(safe_float(rec.get("dynamic_pe")), safe_float(rec.get("yoy_pct")))
