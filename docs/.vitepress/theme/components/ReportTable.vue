<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { withBase } from 'vitepress'

interface RowLink {
  label: string
  url: string
}

interface Row {
  year: number
  quarter: string
  date: string
  code: string
  name: string
  price: number | null
  change_pct: number | null
  market_cap_yi: number | null
  industry: string | null
  np_forecast_yi: number | null
  dynamic_pe: number | null
  cum_pe: number | null
  yoy_pct: number | null
  qoq_pct: number | null
  peg: number | null
  links: string | null
  remark: string | null
  links_list?: RowLink[]
  star_fields: string[]
  cells?: Record<string, string>
}

const raw = ref<any>(null)
const loading = ref(true)
const error = ref('')

const fQuarter = ref('all')
const fIndustry = ref('all')
const fDate = ref('all')
const query = ref('')

onMounted(async () => {
  try {
    const res = await fetch(withBase('/data/reports.json'))
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    raw.value = await res.json()
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})

const allRows = computed<Row[]>(() => {
  if (!raw.value?.days) return []
  return raw.value.days.flatMap((d: any) => d.rows as Row[])
})
const quarters = computed<string[]>(() => raw.value?.quarters ?? [])
const industries = computed<string[]>(() => raw.value?.industries ?? [])
const dates = computed<string[]>(() => (raw.value?.days ?? []).map((d: any) => d.date))

const filtered = computed(() =>
  allRows.value.filter((r) => {
    if (fQuarter.value !== 'all' && `${r.year}/${r.quarter}` !== fQuarter.value) return false
    if (fIndustry.value !== 'all' && r.industry !== fIndustry.value) return false
    if (fDate.value !== 'all' && r.date !== fDate.value) return false
    const q = query.value.trim().toLowerCase()
    if (q) {
      const hay = [r.code, r.name, r.industry, r.remark, r.date]
        .map((v) => String(v ?? '').toLowerCase())
        .join(' ')
      if (!hay.includes(q)) return false
    }
    return true
  }),
)

function display(r: Row, field: keyof Row): string {
  const v = r[field] as unknown
  if (v === null || v === undefined || v === '') {
    return r.cells?.[field as string] ?? '—'
  }
  if (typeof v === 'number') {
    switch (field) {
      case 'price':
      case 'market_cap_yi':
      case 'np_forecast_yi':
      case 'dynamic_pe':
      case 'cum_pe':
      case 'peg':
        return v.toFixed(2)
      case 'change_pct':
        return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
      case 'yoy_pct':
      case 'qoq_pct':
        return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
    }
  }
  return String(v)
}

function star(r: Row, field: string): boolean {
  return (r.star_fields ?? []).includes(field) && r[field as keyof Row] != null
}

function reset() {
  fQuarter.value = 'all'
  fIndustry.value = 'all'
  fDate.value = 'all'
  query.value = ''
}
</script>

<template>
  <div class="qf-dashboard">
    <div v-if="loading" class="qf-status">数据加载中…</div>
    <div v-else-if="error" class="qf-status qf-error">
      数据加载失败：{{ error }}（请先运行 <code>python run.py</code> 生成数据）
    </div>
    <template v-else>
      <div class="qf-filters">
        <label>
          <span>季度</span>
          <select v-model="fQuarter">
            <option value="all">全部</option>
            <option v-for="q in quarters" :key="q" :value="q">{{ q }}</option>
          </select>
        </label>
        <label>
          <span>行业</span>
          <select v-model="fIndustry">
            <option value="all">全部</option>
            <option v-for="i in industries" :key="i" :value="i">{{ i }}</option>
          </select>
        </label>
        <label>
          <span>日期</span>
          <select v-model="fDate">
            <option value="all">全部</option>
            <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
          </select>
        </label>
        <label class="qf-search">
          <span>搜索</span>
          <input v-model="query" type="search" placeholder="代码 / 名称 / 关键词" />
        </label>
        <button class="qf-reset" @click="reset">重置</button>
      </div>

      <div class="qf-stats">
        <div class="qf-card"><b>{{ filtered.length }}</b><span>筛选结果</span></div>
        <div class="qf-card"><b>{{ raw.total_rows ?? 0 }}</b><span>累计记录</span></div>
        <div class="qf-card"><b>{{ dates[0] ?? '—' }}</b><span>最近日期</span></div>
        <div class="qf-card"><b>{{ industries.length }}</b><span>覆盖行业</span></div>
      </div>

      <div class="qf-table-wrap">
        <table class="qf-table">
          <thead>
            <tr>
              <th>日期</th>
              <th>代码</th>
              <th>股票</th>
              <th class="num">股价</th>
              <th class="num">当日涨跌幅</th>
              <th class="num">总市值(亿)</th>
              <th>行业</th>
              <th class="num">机构预期利润(亿)</th>
              <th class="num">Q3动态PE(中值)</th>
              <th class="num">累计Q3动态PE</th>
              <th class="num">Q3扣非同比</th>
              <th class="num">Q3扣非环比</th>
              <th class="num">PEG</th>
              <th>公告/研报链接</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in filtered" :key="`${r.date}-${r.code}`">
              <td class="qf-nowrap">{{ r.date }}</td>
              <td class="qf-mono">{{ r.code }}</td>
              <td class="qf-nowrap">{{ r.name }}</td>
              <td class="num">{{ display(r, 'price') }}</td>
              <td class="num">{{ display(r, 'change_pct') }}</td>
              <td class="num">{{ display(r, 'market_cap_yi') }}</td>
              <td>{{ display(r, 'industry') }}</td>
              <td class="num">
                {{ display(r, 'np_forecast_yi') }}<sup v-if="star(r, 'np_forecast_yi')" class="qf-star">*</sup>
              </td>
              <td class="num">
                {{ display(r, 'dynamic_pe') }}<sup v-if="star(r, 'dynamic_pe')" class="qf-star">*</sup>
              </td>
              <td class="num">{{ display(r, 'cum_pe') }}</td>
              <td class="num">{{ display(r, 'yoy_pct') }}</td>
              <td class="num">{{ display(r, 'qoq_pct') }}</td>
              <td class="num">{{ display(r, 'peg') }}<sup v-if="star(r, 'peg')" class="qf-star">*</sup></td>
              <td class="qf-links">
                <template v-if="r.links_list && r.links_list.length">
                  <a
                    v-for="l in r.links_list"
                    :key="l.url"
                    :href="l.url"
                    target="_blank"
                    rel="noopener"
                    class="qf-link"
                  >{{ l.label }}</a>
                </template>
                <template v-else>{{ display(r, 'links') }}</template>
              </td>
              <td class="qf-remark">{{ display(r, 'remark') }}</td>
            </tr>
            <tr v-if="!filtered.length">
              <td colspan="14" class="qf-empty">没有匹配记录</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="qf-legend">
        <sup class="qf-star">*</sup> 表示脚本自动生成、建议人工核验；人工 override 的字段不带星号。
        「公告/研报链接」为自动归集（可点击查看原文），「备注」列为人工修改。
        数据生成于 {{ raw.generated_at }}。
      </p>
    </template>
  </div>
</template>
