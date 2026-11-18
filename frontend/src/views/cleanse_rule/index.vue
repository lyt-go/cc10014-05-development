<template>
  <section class="page" data-module="cleanse_rule">
    <header class="page-head">
      <div>
        <h2>清洗策略管理</h2>
        <p class="page-desc">维护清洗策略的输入、执行约束与结果记录，为数据清洗提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记清洗策略</button>
        <button class="btn" data-testid="open-reports" type="button" @click="openReports">清洗规则执行报告</button>
        <button class="btn" data-testid="run-group" type="button" :disabled="executing" @click="runGroup">
          {{ executing ? '整组清洗中…' : '整组清洗并生成报告' }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出清洗策略清单</button>
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

    <form class="filter-bar" data-testid="group-form" @submit.prevent="runGroup">
      <label class="filter-item">
        <span>目标数据集</span>
        <input v-model="groupDataset" data-testid="group-dataset" placeholder="如 ods_orders" />
      </label>
      <label class="filter-item">
        <span>报告超时阈值（秒）</span>
        <input v-model="groupTimeout" data-testid="group-timeout" placeholder="默认 5" />
      </label>
      <label class="filter-item">
        <span>超时重试次数</span>
        <input v-model="groupRetries" data-testid="group-retries" placeholder="默认 2" />
      </label>
      <button class="btn" data-testid="submit-group" type="submit" :disabled="executing">
        {{ executing ? '整组清洗中…' : '执行整组清洗' }}
      </button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
            <button class="link" type="button" :data-testid="`execute-${row.id}`" @click="executeRow(row)">
              整组清洗
            </button>
            <button class="link" type="button" :data-testid="`report-link-${row.id}`" @click="openReportForRow(row)">
              执行报告
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无清洗策略数据，可先登记清洗策略</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记清洗策略</h3>
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
      <span>共 {{ total }} 条清洗策略记录</span>
      <span class="page-hint">整组清洗会按执行顺序串联规则，并生成保留命中数与替换值的执行报告</span>
      <span v-if="noticeMessage" class="notice-text" data-testid="group-result">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/cleanse_rule'
const router = useRouter()
const columns = ["策略编号", "目标字段", "空值处理", "异常阈值", "正则表达式", "替换值", "策略版本", "执行顺序"]
const actions = ["提交校验", "标记生效", "停用策略"]
const statuses = ["草稿", "待校验", "已生效", "已停用"]
const metricNames = ["清洗策略总数", "待校验策略", "已生效策略"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const executing = ref(false)
const filters = ref<Record<string, string>>({})
const groupDataset = ref('ods_orders')
const groupTimeout = ref('')
const groupRetries = ref('')
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
    link.download = 'cleanse_rule.csv'
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
      throw new Error('清洗策略动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清洗策略操作失败'
  }
}

function openReports() {
  void router.push('/cleanse_rule/reports')
}

async function runGroup() {
  errorMessage.value = ''
  noticeMessage.value = ''
  const dataset = groupDataset.value.trim()
  if (!dataset) {
    errorMessage.value = '请指定目标数据集后再执行整组清洗'
    return
  }
  executing.value = true
  try {
    const body: Record<string, string> = { 目标数据集: dataset }
    if (groupTimeout.value.trim()) body['超时阈值'] = groupTimeout.value.trim()
    if (groupRetries.value.trim()) body['重试次数'] = groupRetries.value.trim()
    const response = await request(`${ENDPOINT}/execute`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '整组清洗失败')
    noticeMessage.value = payload.message
    if (payload.entry?.['报告编号']) {
      await router.push(`/cleanse_rule/reports/${payload.entry['报告编号']}`)
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '整组清洗失败'
  } finally {
    executing.value = false
  }
}

async function executeRow(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/execute`, { method: 'POST', body: JSON.stringify({}) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '整组清洗失败')
    noticeMessage.value = payload.message
    if (payload.entry?.['报告编号']) {
      await router.push(`/cleanse_rule/reports/${payload.entry['报告编号']}`)
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '整组清洗失败'
  }
}

async function openReportForRow(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/reports?dataset=${encodeURIComponent(String(row['目标数据集'] ?? ''))}`)
    if (!response.ok) throw new Error('执行报告读取失败')
    const payload = await response.json()
    const latest = payload.items?.[0]
    if (!latest) {
      errorMessage.value = '该数据集还没有清洗规则执行报告，请先执行整组清洗'
      return
    }
    await router.push(`/cleanse_rule/reports/${latest['报告编号']}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '执行报告读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('清洗策略列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清洗策略列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.notice-text { color: #067647; }
.page-hint { color: var(--muted); }
</style>
