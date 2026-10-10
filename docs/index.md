---
layout: home

hero:
  name: 季度财报跟踪
  text: 每日财报自动采集 · 人工可干预 · 季度循环归档
  tagline: 行情 / 扣非财务 / 机构预期自动入库，Markdown 归档 + 可视化筛选看板，GitHub Actions 自动部署
  actions:
    - theme: brand
      text: 打开筛选看板
      link: /dashboard
    - theme: alt
      text: 查看每日报告
      link: /reports/2025/Q3/2025-10-09

features:
  - title: 一键运行
    details: python run.py --date 2025-10-09 --codes 688099,600176,300373，自动完成采集、指标计算与归档。
  - title: 人工优先
    details: manual/ 目录字段级 override 永不覆盖；手工编辑 Markdown 的行会被永久保护，重跑只追加新股票。
  - title: 可追溯
    details: 按 年份/季度/日期 归档 Markdown 与原始 JSON，Git 历史区分自动更新与人工修正。
  - title: 自动部署
    details: push 后 GitHub Actions 自动构建并发布本页面。
---
