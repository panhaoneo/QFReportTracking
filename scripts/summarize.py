"""增长原因简述生成。

优先级：人工 override > LLM（OpenAI 兼容接口，需配置环境变量）> 启发式模板。
无论哪种自动方式，结果都会被标记 *（待人工核验）。

LLM 环境变量：
  LLM_API_KEY   必填，接口密钥（如 DeepSeek/OpenAI/通义等兼容服务的 key）
  LLM_BASE_URL  可选，默认 https://api.openai.com/v1
  LLM_MODEL     可选，默认 gpt-4o-mini
"""
from __future__ import annotations

import json
import os

from .utils import fmt_pct, get_logger

logger = get_logger()

_SYSTEM_PROMPT = (
    "你是A股财报分析助理。基于给定的结构化数据，用不超过60字的一句话概括该公司本季度"
    "业绩变动的最可能原因。只允许使用给定信息，数据不足时如实说明，不要编造。"
    "输出纯文本，不要markdown或标点列表。"
)


def _llm_summary(rec: dict) -> str | None:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return None
    try:
        import requests
    except ImportError:
        logger.warning("未安装 requests，跳过 LLM 总结")
        return None

    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    payload = {
        "code": rec.get("code"),
        "name": rec.get("name"),
        "report_period": rec.get("report_period"),
        "industry": rec.get("industry"),
        "market_cap_yi": rec.get("market_cap_yi"),
        "change_pct_today": rec.get("change_pct"),
        "q3_single_quarter_deduct_net_profit_yi": rec.get("q_deduct_yi"),
        "q3_yoy_pct": rec.get("yoy_pct"),
        "q3_qoq_pct": rec.get("qoq_pct"),
        "institution_forecast": rec.get("forecast"),
        "recent_news_titles": (rec.get("news") or [])[:5],
    }
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception as exc:  # 网络/接口异常时静默降级到启发式
        logger.warning("[%s] LLM 总结失败（%s），改用启发式", rec.get("code"), exc)
        return None


def _heuristic_summary(rec: dict) -> str:
    parts: list[str] = []
    label = str(rec.get("report_period") or "")
    yoy, qoq = rec.get("yoy_pct"), rec.get("qoq_pct")
    if yoy is not None or qoq is not None:
        seg = f"{label}单季扣非净利"
        if yoy is not None:
            seg += f"同比{fmt_pct(yoy)}"
        if qoq is not None:
            seg += f"、环比{fmt_pct(qoq)}"
        parts.append(seg)
    forecast = rec.get("forecast") or {}
    if forecast.get("growth_median_pct") is not None:
        parts.append(f"机构预期全年增速约{fmt_pct(forecast['growth_median_pct'])}")
    news = rec.get("news") or []
    if news:
        parts.append(f"相关资讯《{str(news[0])[:40]}》")
    if not parts:
        return "数据不足，未能自动归纳，请人工填写增长原因"
    return "；".join(parts) + "（自动归纳，请人工核验）"


def generate_growth_reason(rec: dict, use_llm: bool = True) -> str:
    if use_llm:
        text = _llm_summary(rec)
        if text:
            return text
    return _heuristic_summary(rec)
