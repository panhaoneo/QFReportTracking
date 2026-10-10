# QFReportTracking · 季度财报自动化跟踪

每日输入当天发布财报的股票代码，自动完成：**数据采集 → 指标计算 → 增长原因归纳 → Markdown 增量归档 → 站点看板更新**。
支持按季度循环使用（4月/8月/10月…），支持人工修正且**人工结果永不被覆盖**，push 后 GitHub Actions 自动发布 GitHub Pages。

## 目录结构

```
run.py                     # 一键入口（编排全部步骤）
scripts/
  fetch_data.py            # 数据采集（akshare 为主，tushare 可选，offline 缓存）
  calc_metrics.py          # 指标计算（PE / 累计PE / 同比 / 环比 / PEG）
  merge_manual.py          # 合并 manual/ 人工覆盖（优先级最高）
  summarize.py             # 增长原因（LLM 可选，默认启发式模板）
  generate_md.py           # Markdown 增量生成（人工修改保护）
  update_site.py           # 解析 reports/ 生成站点数据（纯标准库，CI 可用）
  config.py / utils.py     # 配置与工具
reports/2025/Q3/*.md       # 归档报告（最终产物，可人工直接编辑）
raw/2025/Q3/*.json         # 自动抓取的原始数据 + 行哈希（脚本可覆盖，勿手改）
manual/2025/Q3/*.json      # 人工修正（脚本永不覆盖）
docs/                      # VitePress 站点（看板 + 报告副本 + 数据 JSON）
.github/workflows/deploy.yml
```

## 快速开始

```bash
# 1) 安装依赖（Python 3.10+ / Node 18+）
pip install -r requirements.txt
npm install

# 2) 每日运行（示例：2025-10-09 的 3 只财报股）
python run.py --date 2025-10-09 --codes 688099,600176,300373

# 3) 本地预览站点
npm run docs:dev

# 4) 提交（触发 GitHub Actions 自动部署）
git add -A && git commit -m "auto: 2025-10-09 财报记录" && git push
```

首次部署需在 GitHub 仓库 **Settings → Pages → Source 选择 “GitHub Actions”**。
项目站点路径默认 `/<repo>/`；自定义域名时构建命令改为 `DOCS_BASE=/ npm run docs:build`（deploy.yml 中加 env）。

### 常用参数

| 参数 | 说明 |
|---|---|
| `--date` | 运行日期（必填），决定归档文件 `reports/YYYY/Qn/YYYY-MM-DD.md` |
| `--codes` | 当日发布财报的股票代码，逗号分隔（必填） |
| `--quarter` / `--year` | 报告期，缺省按日期推断：1-3月→上年Q4，4-6月→Q1，7-8月→Q2，9-12月→Q3 |
| `--industry "688099=端侧AI,…"` | 行业兜底（抓取不到行业时使用） |
| `--offline` | 不联网，复用 `raw/` 缓存（演示/断网时复跑） |
| `--no-llm` | 禁用 LLM 总结，仅用启发式模板 |
| `--git-commit` | 生成后自动 `git add + commit`（不会 push） |

重复运行同一天会**先刷新脚本生成的行、追加新股票、跳过人工改过的行**，可安全多次执行。

## 数据源

| 维度 | 主源 | 兜底 |
|---|---|---|
| 行情（价格/涨跌幅/总市值） | 腾讯行情 qt.gtimg.cn | 东财快照、tushare |
| 行业 | 东财个股信息 | 巨潮公司概况、`--industry`、manual |
| 扣非净利润（单季/同比/环比） | 同花顺「按单季度」财务摘要 | 新浪财务指标（累计差分）、tushare |
| 机构预期（全年净利润） | 同花顺机构盈利预测（均值≈中值，字段注明） | 东财盈利预测；缺失标记 `无预期*` |
| 增长原因线索 | 东财个股新闻、巨潮公告 | — |

- **LLM 归纳（可选）**：配置 `LLM_API_KEY`，可选 `LLM_BASE_URL`（默认 OpenAI 兼容地址）、`LLM_MODEL`。未配置时用启发式模板。两种结果都带 `*` 待核验。
- **tushare（可选）**：配置 `TUSHARE_TOKEN` 后作为行情/行业兜底，不配置不影响主流程。

## 指标公式（口径明确）

| 指标 | 公式 |
|---|---|
| Q3动态PE(中值) | 总市值(亿) ÷ 机构预测**全年**净利润中值(亿)；无预期 → `无预期*` |
| 累计Q3动态PE | 总市值(亿) ÷ (前三季度**累计扣非净利润**(亿) × 4/3) |
| Q3扣非净利润同比 | (Q3单季扣非 − 去年同期单季扣非) ÷ \|去年同期单季扣非\| × 100% |
| Q3扣非净利润环比 | (Q3单季扣非 − Q2单季扣非) ÷ \|Q2单季扣非\| × 100% |
| PEG | Q3动态PE(中值) ÷ Q3扣非净利润同比**百分数数值**（如 PE 15.63、同比 290% → 0.05；同比≤0 时显示 `—`） |

单季扣非 = 累计扣非差分（Q3单季 = 前三季累计 − 中报累计）。列名中的 “Q3” 随报告期自动变化（Q2 季运行时显示 “Q2动态PE(中值)” 等）。

## 人工干预（Human-in-the-Loop）

**`*` 的含义**：脚本/AI 生成、建议核验的字段（动态PE、PEG、增长原因）；人工确认过的字段不带 `*`。

### 方式 A：直接改 Markdown（应急）
直接编辑 `reports/.../日期.md` 的行。脚本通过行哈希识别：**人工改过的行会被永久保留**，重跑只刷新未被手改的行、追加新股票。

### 方式 B：改 manual JSON（推荐，Git diff 清晰）
编辑 `manual/YYYY/Qn/YYYY-MM-DD.json`（脚本永不覆盖该目录）：

```json
{
  "date": "2025-10-09",
  "overrides": {
    "688099": {
      "np_median_yi": 28.5,
      "yoy_pct": 280,
      "growth_reason": "手动修正的表述",
      "manual_note": "个人备注",
      "institution_expectation": "8家机构全年净利预测约27亿"
    }
  }
}
```

支持的 override 字段：`price` `change_pct` `market_cap_yi` `industry` `np_median_yi`（联动重算 PE/PEG）
`q_deduct_yi` `prev_year_q_deduct_yi` `prev_q_deduct_yi`（单季扣非成分，联动重算同比/环比，适合业绩预告期按区间中值推算）
`dynamic_pe` `cum_pe` `yoy_pct` `qoq_pct` `peg`（直接覆盖衍生指标）
`growth_reason` `manual_note` `institution_expectation`（文本；后者展示在人工备注列）。数值字段非法值会被忽略并告警。

## 容错与增量

- 单只股票采集失败：记录到日志与 `raw.generated` 的 `failed` 列表并跳过，不影响其他股票；关键字段全失败的股票不生成行。
- 缺失值显示 `—`；机构预期缺失显示 `无预期*`。
- 全流程幂等：无新数据时重跑不产生任何 diff（统计脚注仅在行变化时更新）。
- `raw/` 中的行哈希是识别“人工手改”的依据，**请勿手动删除 raw**；删除后既有行会被保守视为人工内容、不再自动刷新。

## 回滚

```bash
git checkout -- reports/2025/Q3/2025-10-09.md   # 恢复单个报告文件
git checkout -- manual/2025/Q3/2025-10-09.json  # 恢复人工覆盖文件
git revert <commit>                              # 整体回滚一次自动更新并保留历史
```

自动更新与人工修改建议分开 commit（`--git-commit` 生成的 message 为 `auto: …`，人工修改自行填写），Git 历史即可区分两类修改。

## 每季度复用

1. 新季度直接开始每天运行，无需改配置：`python run.py --date 2026-10-15 --codes …`，报告自动落到 `reports/2026/Q3/`。
2. 报告期推断不符合预期时用 `--quarter` / `--year` 显式指定。
3. `manual/YYYY/Qn/` 按季度建立覆盖文件即可。

## 仓库内置示例数据

`raw/`、`manual/`、`reports/` 下预置了 `2025-10-09 · 688099 晶晨股份` 的演示数据（fixture，非真实抓取），用于离线体验完整流程：

```bash
python run.py --date 2025-10-09 --codes 688099 --offline --no-llm
```

真实使用时可删除该演示文件，或直接按日运行覆盖。

## FAQ

- **行业显示为 `—`**：东财个股信息接口偶发不可达；用 `--industry "688099=端侧AI"` 或在 manual 中覆盖 `industry`。
- **某天接口大面积失败**：先 `--offline` 用缓存复跑；akshare 接口变更时在 `scripts/fetch_data.py` 中补充候选数据源即可。
- **想看某天的报告网页**：站点左侧「每日报告」按 年/季度 分组列出所有日期；也可直接看 `reports/` 目录。
- **PEG 与常见口径不同？** 本工具按项目约定 `PE ÷ 同比百分数`（如 290 而非 2.9），与表格示例一致。
