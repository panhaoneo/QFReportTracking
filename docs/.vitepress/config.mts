import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitepress'

// GitHub Pages 项目站点默认部署在 /<repo>/ 下；自定义域名/用户站点时用 DOCS_BASE=/ 覆盖
const base = process.env.DOCS_BASE || '/QFReportTracking/'

const currentDir = path.dirname(fileURLToPath(import.meta.url))

function reportsSidebar() {
  const file = path.resolve(currentDir, 'reports-sidebar.json')
  if (!fs.existsSync(file)) return []
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf-8'))
    return (data.groups || []).map((group: any, i: number) => ({
      text: group.text,
      collapsed: i > 0,
      items: group.items,
    }))
  } catch {
    return []
  }
}

export default defineConfig({
  base,
  lang: 'zh-CN',
  title: '季度财报跟踪',
  description: '每日财报自动采集 · 人工可干预 · 季度循环归档',
  lastUpdated: true,
  themeConfig: {
    nav: [
      { text: '筛选看板', link: '/dashboard' },
    ],
    sidebar: {
      '/reports/': reportsSidebar(),
    },
    search: {
      provider: 'local',
    },
    outline: { label: '本页目录', level: [2, 3] },
    docFooter: { prev: '上一篇', next: '下一篇' },
    lastUpdated: { text: '最后更新' },
    returnToTopLabel: '回到顶部',
    sidebarMenuLabel: '目录',
    darkModeSwitchLabel: '主题',
  },
})
