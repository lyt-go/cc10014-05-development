<template>
  <section class="page" data-module="cleanse_rule_report">
    <header class="page-head">
      <div>
        <h2>清洗规则执行报告</h2>
        <p class="page-desc">
          按执行顺序复核整组清洗结果：每条规则保留命中数与替换值，同字段冲突先判定优先策略，
          版本顺序与命中范围两种历史片段分别留存。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="back-to-list" @click="goBack">返回清洗策略</button>
        <button class="btn primary" type="button" data-testid="export-report" @click="exportReports">导出报告清单</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="reloadList">
      <label class="filter-item">
        <span>目标数据集</span>
        <input v-model="dataset" data-testid="filter-dataset" placeholder="按目标数据集检索" />
      </label>
      <button class="btn" type="submit" data-testid="query">查询</button>
      <button class="btn ghost" type="button" data-testid="reload" @click="loadDetail">重新核对</button>
    </form>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <table class="data-table" data-testid="report-list">
      <thead>
        <tr>
          <th v-for="column in reportColumns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in reports"
          :key="String(row['报告编号'])"
          :data-testid="`report-row-${row['报告编号']}`"
          :class="{ selected: row['报告编号'] === activeReportNo }"
        >
          <td v-for="column in reportColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`open-report-${row['报告编号']}`" @click="openReport(row)">
              查看详情
            </button>
          </td>
        </tr>
        <tr v-if="!reports.length">
          <td :colspan="reportColumns.length + 1" class="empty-state">暂无清洗规则执行报告，可在清洗策略页执行整组清洗</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="report-detail" data-testid="report-detail">
      <h3>报告 {{ detail['报告编号'] }} · {{ detail['目标数据集'] }}</h3>
      <div class="detail-grid">
        <dl><dt>明细表编号</dt><dd data-testid="detail-dwd">{{ detail['明细表编号'] || '—' }}</dd></dl>
        <dl><dt>执行状态</dt><dd>{{ detail['执行状态'] }}</dd></dl>
        <dl><dt>生成时间</dt><dd>{{ detail['生成时间'] }}</dd></dl>
        <dl><dt>待清洗/清洗后行数</dt><dd>{{ detail['待清洗行数'] }} / {{ detail['清洗后行数'] }}</dd></dl>
        <dl><dt>超时阈值</dt><dd>{{ detail['超时阈值'] }} 秒</dd></dl>
        <dl><dt>重试次数</dt><dd data-testid="detail-retries">{{ detail['重试次数'] }}</dd></dl>
        <dl class="wide"><dt>重试原因</dt><dd data-testid="detail-retry-reason">{{ retryReason }}</dd></dl>
      </div>

      <h4>规则明细（命中数与替换值）</h4>
      <table class="data-table" data-testid="rule-detail-table">
        <thead>
          <tr>
            <th>执行顺序</th><th>策略编号</th><th>策略版本</th><th>目标字段</th><th>异常阈值</th>
            <th>空值处理</th><th>命中数</th><th>空值命中</th><th>正则命中</th><th>替换值</th><th>冲突</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in detail['规则明细']" :key="rule['策略编号']" :data-testid="`rule-row-${rule['策略编号']}`">
            <td>{{ rule['执行顺序'] }}</td>
            <td>{{ rule['策略编号'] }}</td>
            <td>{{ rule['策略版本'] }}</td>
            <td>{{ rule['目标字段'] }}</td>
            <td>{{ rule['异常阈值'] || '—' }}</td>
            <td>{{ rule['空值处理'] || '—' }}</td>
            <td data-testid="hit-count">{{ rule['命中数'] }}</td>
            <td>{{ rule['空值命中数'] }}</td>
            <td>{{ rule['正则命中数'] }}</td>
            <td data-testid="replacement">{{ rule['替换值'] || '—' }}</td>
            <td>{{ rule['冲突'] ? '是' : '否' }}</td>
          </tr>
          <tr v-if="!detail['规则明细']?.length">
            <td colspan="11" class="empty-state">本报告没有可执行规则</td>
          </tr>
        </tbody>
      </table>

      <h4>冲突判定（优先处理哪条清洗策略）</h4>
      <table v-if="detail['冲突判定']?.length" class="data-table" data-testid="conflict-table">
        <thead>
          <tr><th>目标字段</th><th>冲突规则</th><th>优先策略</th><th>优先版本</th><th>判定依据</th></tr>
        </thead>
        <tbody>
          <tr v-for="conflict in detail['冲突判定']" :key="conflict['目标字段']">
            <td>{{ conflict['目标字段'] }}</td>
            <td>{{ conflict['冲突规则'].map((item: any) => `${item['策略编号']} ${item['策略版本']}（顺序${item['执行顺序']}）`).join('、') }}</td>
            <td data-testid="priority-rule">{{ conflict['优先策略'] }}</td>
            <td>{{ conflict['优先版本'] }}</td>
            <td>{{ conflict['判定依据'] }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-hint" data-testid="no-conflict">本次执行没有同字段版本冲突</p>

      <div class="view-grid">
        <section>
          <h4>按版本顺序列出替换值</h4>
          <table class="data-table" data-testid="version-view">
            <thead><tr><th>历史片段</th><th>策略编号</th><th>策略版本</th><th>执行顺序</th><th>命中数</th><th>替换值</th></tr></thead>
            <tbody>
              <tr v-for="item in detail['版本顺序替换视图']" :key="`${item['策略编号']}-${item['历史片段']}`">
                <td>{{ item['历史片段'] }}</td>
                <td>{{ item['策略编号'] }}</td>
                <td>{{ item['策略版本'] }}</td>
                <td>{{ item['执行顺序'] }}</td>
                <td>{{ item['命中数'] }}</td>
                <td>{{ item['替换值'] || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section>
          <h4>按命中范围列出替换值</h4>
          <table class="data-table" data-testid="hit-range-view">
            <thead><tr><th>历史片段</th><th>命中范围</th><th>规则数</th><th>替换值</th></tr></thead>
            <tbody>
              <tr v-for="item in detail['命中范围替换视图']" :key="item['历史片段']">
                <td>{{ item['历史片段'] }}</td>
                <td>{{ item['命中范围'] }}</td>
                <td>{{ item['规则数'] }}</td>
                <td>
                  <span v-for="entry in item['替换值']" :key="entry['策略编号']" class="chip">
                    {{ entry['目标字段'] }}= {{ entry['替换值'] || '—' }}（{{ entry['命中数'] }}）
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <h4>DWD刷新口径（替换值逐字段对应）</h4>
      <table class="data-table" data-testid="refresh-table">
        <thead><tr><th>明细表编号</th><th>待刷新</th><th>刷新原因</th></tr></thead>
        <tbody>
          <tr v-for="(note, index) in detail['DWD刷新']" :key="index">
            <td>{{ note['表编号'] || '—' }}</td>
            <td>{{ note['待刷新'] ? '是' : '否' }}</td>
            <td data-testid="refresh-reason">{{ note['刷新原因'] }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

const props = defineProps<{ reportNo?: string }>()
const router = useRouter()

const ENDPOINT = '/api/cleanse_rule'
const reportColumns = ["报告编号", "目标数据集", "明细表编号", "执行状态", "生成时间", "规则总数", "重试次数"]
const dataset = ref('')
const reports = ref<Record<string, any>[]>([])
const total = ref(0)
const detail = ref<Record<string, any> | null>(null)
const activeReportNo = ref('')
const errorMessage = ref('')

const retryReason = computed(() => {
  const reasons = detail.value?.['重试原因'] ?? []
  return reasons.length ? reasons.join('；') : '未触发重试'
})
const stats = computed(() => [
  { label: '报告总数', value: total.value },
  { label: '规则总数', value: detail.value?.['规则总数'] ?? 0 },
  { label: '命中总数', value: (detail.value?.['规则明细'] ?? []).reduce((sum: number, item: any) => sum + Number(item['命中数'] ?? 0), 0) },
])

function goBack() {
  void router.push('/cleanse_rule')
}

async function exportReports() {
  errorMessage.value = ''
  const query = new URLSearchParams(dataset.value ? { dataset: dataset.value } : {}).toString()
  try {
    const response = await request(`${ENDPOINT}/reports/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'cleanse_rule_report.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

async function reloadList() {
  errorMessage.value = ''
  const params: Record<string, string> = {}
  if (dataset.value) params.dataset = dataset.value
  try {
    const response = await request(`${ENDPOINT}/reports?${new URLSearchParams(params).toString()}`)
    if (!response.ok) throw new Error('清洗规则执行报告列表读取失败')
    const payload = await response.json()
    reports.value = payload.items ?? []
    total.value = payload.total ?? reports.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清洗规则执行报告列表读取失败'
  }
}

async function openReport(row: Record<string, any>) {
  await router.push(`/cleanse_rule/reports/${row['报告编号']}`)
}

async function loadDetail() {
  errorMessage.value = ''
  const reportNo = props.reportNo || activeReportNo.value
  if (!reportNo) {
    detail.value = null
    return
  }
  try {
    const response = await request(`${ENDPOINT}/reports/${reportNo}`)
    if (!response.ok) throw new Error(`清洗规则执行报告 ${reportNo} 读取失败`)
    detail.value = await response.json()
    activeReportNo.value = String(detail.value?.['报告编号'] ?? reportNo)
  } catch (error) {
    detail.value = null
    errorMessage.value = error instanceof Error ? error.message : '报告详情读取失败'
  }
}

watch(() => props.reportNo, loadDetail)

onMounted(async () => {
  await reloadList()
  await loadDetail()
})
</script>

<style scoped>
.report-detail { margin-top: 20px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
.report-detail h3 { margin: 0 0 12px; font-size: 15px; }
.report-detail h4 { margin: 18px 0 8px; font-size: 13px; color: var(--muted); }
.detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 16px; }
.detail-grid .wide { grid-column: 1 / -1; }
.detail-grid dt { font-size: 12px; color: var(--muted); }
.detail-grid dd { margin: 2px 0 0; font-size: 13px; }
.view-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.chip { display: inline-block; margin: 2px 6px 2px 0; padding: 2px 8px; border: 1px solid var(--border); border-radius: 10px; font-size: 12px; }
.empty-hint { color: var(--muted); font-size: 13px; }
.data-table tr.selected { background: #eef4ff; }
.notice-text { color: #067647; }
</style>
