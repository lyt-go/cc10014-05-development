<template>
  <section class="page" data-module="dwd_layer">
    <header class="page-head">
      <div>
        <h2>明细层管理管理</h2>
        <p class="page-desc">维护明细层DWD表的输入、执行约束与结果记录，为明细层管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记明细表</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出明细层管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in displayColumns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in displayColumns" :key="column">{{ formatCell(row, column) }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`build-${row.id}`" @click="buildRow(row)">构建明细</button>
            <button class="link" type="button" :data-testid="`report-link-${row.id}`" @click="openReport(row)">清洗报告</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="displayColumns.length + 1" class="empty-state">暂无明细层管理数据，可先登记明细表</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记明细表</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条明细层管理记录</span>
      <span v-if="noticeMessage" class="notice-text" data-testid="build-result">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | null | boolean>

const ENDPOINT = '/api/dwd_layer'
const router = useRouter()
const columns = ["表编号", "父层来源", "表名称", "粒度说明", "分区字段", "关联键列", "刷新水位", "创建批次"]
// 列表额外展示刷新标记与最近报告，方便进入清洗规则执行报告详情逐字段核对
const extraColumns = ["刷新状态", "最近清洗报告"]
const displayColumns = [...columns, ...extraColumns]
const actions = ["提交校验", "确认上线", "标记下线"]
const statuses = ["草稿", "待校验", "已上线", "已下线"]
const metricNames = ["明细表总数", "待校验表", "已上线表"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'dwd_layer.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('明细层管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '明细层管理操作失败'
  }
}

function formatCell(row: Row, column: string): string {
  if (column === '刷新状态') {
    if (row['待刷新']) return '待刷新'
    return row['最近报告'] ? '已按报告刷新' : '—'
  }
  if (column === '最近清洗报告') return String(row['最近报告'] ?? '—')
  return String(row[column] ?? '—')
}

async function buildRow(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/build`, { method: 'POST', body: '{}' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '明细构建失败')
    noticeMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '明细构建失败'
  }
}

async function openReport(row: Row) {
  errorMessage.value = ''
  const reportNo = String(row['最近报告'] ?? '')
  if (reportNo) {
    await router.push(`/cleanse_rule/reports/${reportNo}`)
    return
  }
  // 没有报告时按父层ODS表名找该数据集最近一份报告，找不到提示先执行清洗
  try {
    let dataset = String(row['父层来源'] ?? '')
    const odsResponse = await request(`/api/ods_layer?size=200`)
    if (odsResponse.ok) {
      const odsPayload = await odsResponse.json()
      const ods = (odsPayload.items ?? []).find((item: any) =>
        item['表编号'] === row['父层来源'] || item['表名称'] === row['父层来源'])
      if (ods) dataset = String(ods['表名称'] ?? dataset)
    }
    const response = await request(`/api/cleanse_rule/reports?dataset=${encodeURIComponent(dataset)}`)
    if (!response.ok) throw new Error('清洗规则执行报告读取失败')
    const payload = await response.json()
    const latest = payload.items?.[0]
    if (!latest) {
      errorMessage.value = '该明细表还没有清洗规则执行报告，请先构建明细或执行整组清洗'
      return
    }
    await router.push(`/cleanse_rule/reports/${latest['报告编号']}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清洗规则执行报告读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('明细表列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '明细层管理列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.notice-text { color: #067647; }
</style>
