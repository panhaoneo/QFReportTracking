"""人工修正合并：manual/ 目录中的 overrides 优先级最高，脚本永不覆盖该目录。

manual/YYYY/Qn/YYYY-MM-DD.json 结构：
{
  "date": "2025-10-09",
  "overrides": {
    "688099": {
      "np_median_yi": 28.5,            // 数值：机构预测全年净利润中值(亿)，联动重算 PE/PEG
      "yoy_pct": 280,                  // 数值：直接覆盖同比
      "growth_reason": "手动修正的表述",
      "manual_note": "个人备注",
      "institution_expectation": "某券商 q4 上调至…"
    }
  }
}

合并顺序：
1) 覆盖原始成分（price/market_cap_yi/np_median_yi 等）→ 2) 重算衍生指标
→ 3) 覆盖衍生指标（dynamic_pe/yoy_pct/peg…，显式值优先）→ 4) 覆盖文本字段。
"""
from __future__ import annotations

from . import config as C
from .calc_metrics import compute_metrics, recompute_peg
from .utils import get_logger, load_json, safe_float

logger = get_logger()

# 原始成分字段：覆盖后需整体重算衍生指标
# （q_deduct_yi 等单季/累计扣非用于业绩预告期按区间推算的场景）
_COMPONENT_FIELDS = (
    "price", "change_pct", "market_cap_yi", "cum_deduct_yi",
    "q_deduct_yi", "prev_year_q_deduct_yi", "prev_q_deduct_yi",
)
# 衍生指标字段：直接覆盖，优先级高于重算结果
_DERIVED_FIELDS = ("dynamic_pe", "cum_pe", "yoy_pct", "qoq_pct", "peg")
_TEXT_FIELDS = ("industry", "growth_reason", "manual_note", "institution_expectation")


def load_overrides(year: int, quarter: str, date_str: str) -> dict:
    """读取当日人工覆盖；文件不存在时返回空 dict。"""
    path = C.manual_json_path(year, quarter, date_str)
    data = load_json(path)
    if not data:
        return {}
    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        logger.warning("manual 文件 %s 的 overrides 不是对象，已忽略", path)
        return {}
    return overrides


def apply_overrides(rec: dict, ov: dict) -> set[str]:
    """对单条记录应用 overrides，返回被人工修改的展示字段集合（用于去掉 * 标记）。"""
    manual: set[str] = set()
    if not ov:
        return manual

    # 1) 机构预期数值（联动 PE/PEG）
    np_median = ov.get("np_median_yi")
    if np_median is not None:
        v = safe_float(np_median)
        if v is None:
            logger.warning("[%s] np_median_yi 非法: %r，已跳过", rec.get("code"), np_median)
        else:
            rec.setdefault("forecast", {})
            if not isinstance(rec["forecast"], dict):
                rec["forecast"] = {}
            rec["forecast"]["np_median_yi"] = v
            rec["forecast"]["source"] = "manual"
            manual.update({"dynamic_pe", "peg"})

    # 2) 先覆盖原始成分，再整体重算衍生指标
    for field in _COMPONENT_FIELDS:
        if field in ov:
            v = safe_float(ov[field])
            if v is None:
                logger.warning("[%s] 覆盖字段 %s 非法: %r，已跳过", rec.get("code"), field, ov[field])
                continue
            rec[field] = v
    compute_metrics(rec)

    # 3) 显式覆盖的衍生指标优先；PEG 未显式覆盖时与 yoy/pe 保持联动
    for field in _DERIVED_FIELDS:
        if field in ov:
            v = safe_float(ov[field])
            if v is None:
                logger.warning("[%s] 覆盖字段 %s 非法: %r，已跳过", rec.get("code"), field, ov[field])
                continue
            rec[field] = v
            manual.add(field)
    if "peg" not in ov:
        recompute_peg(rec)

    # 4) 文本字段
    for field in _TEXT_FIELDS:
        if field in ov and ov[field] is not None:
            rec[field] = str(ov[field])
            manual.add(field)

    return manual
