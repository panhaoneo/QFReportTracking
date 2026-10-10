"""通用工具：日志、JSON 读写、数值格式化与解析。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import LOGS_DIR

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """全局 logger：控制台 + logs/run.log 双输出。"""
    global _logger
    if _logger is not None:
        return _logger
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("qfrt")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOGS_DIR / "run.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    _logger = logger
    return logger


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_float(value: Any) -> float | None:
    """尽力把任意输入转成 float，失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f else f  # NaN -> None
    s = str(value).strip().replace(",", "").replace("%", "").replace("+", "")
    if s in ("", "-", "--", "—", "None", "nan", "NaN", "无预期", "无预期*"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------- Markdown 单元格渲染 ----------

def fmt_plain(v: float | None, digits: int = 2) -> str:
    """普通数值：None -> —"""
    return "—" if v is None else f"{v:.{digits}f}"


def fmt_price(v: float | None) -> str:
    return fmt_plain(v, 2)


def fmt_mcap(v: float | None) -> str:
    return fmt_plain(v, 2)


def fmt_pct(v: float | None, digits: int = 1) -> str:
    """带符号百分比：290 -> +290.0%，缺失 -> —"""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{digits}f}%"


def fmt_ratio(v: float | None, digits: int = 2) -> str:
    return fmt_plain(v, digits)


def md_cell_text(s: str | None) -> str:
    """文本单元格清洗：竖线替换为全角、换行折叠，避免破坏表格结构。"""
    if not s:
        return "—"
    return str(s).replace("|", "｜").replace("\r", " ").replace("\n", " ").strip()


# ---------- Markdown 单元格解析（update_site 用） ----------

def parse_cell(text: str) -> float | str | None:
    """将 markdown 表格单元格解析为 数值/文本/None。"""
    s = text.strip()
    if s.endswith("*"):
        s = s[:-1].strip()
    if s in ("", "—", "-", "--"):
        return None
    if s.startswith("无预期"):
        return None
    numeric = safe_float(s)
    if numeric is not None and any(ch.isdigit() for ch in s):
        return numeric
    return s
