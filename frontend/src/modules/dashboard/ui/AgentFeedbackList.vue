<template>
  <section class="feedback-list" aria-label="智能体反馈">
    <div class="feedback-panel">
      <div class="feedback-toolbar" role="group" aria-label="反馈筛选与刷新">
        <div class="filter-field source-field">
          <label for="feedback-source">反馈来源</label>
          <a-select
            id="feedback-source"
            v-model:value="filters.source"
            :options="sourceOptions"
            @change="changeSource"
          />
        </div>
        <div class="filter-field agent-field">
          <label for="feedback-agent">智能体</label>
          <a-select
            id="feedback-agent"
            :value="filters.agent_id"
            :options="agentOptions"
            :loading="agentsLoading"
            show-search
            :filter-option="filterAgentOption"
            @change="selectAgent"
          />
        </div>
        <div v-if="filters.source === 'user'" class="filter-field rating-field">
          <label for="feedback-rating">用户评价</label>
          <a-select id="feedback-rating" v-model:value="filters.rating" :options="ratingOptions" @change="reload" />
        </div>
        <a-button v-if="hasFilters" type="text" class="reset-filters" @click="clearFilters">重置筛选</a-button>
        <div class="toolbar-actions">
          <span class="feedback-count" role="status">{{ loading && !items.length ? '正在加载…' : `${items.length} 条` }}</span>
          <a-button class="refresh-btn" :loading="loading" @click="reload">
            <template #icon><RefreshCw :size="14" aria-hidden="true" /></template>
            刷新
          </a-button>
        </div>
      </div>
      <p v-if="agentsError" class="agent-load-error" role="alert">
        智能体选项加载失败。
        <button type="button" class="text-action" @click="loadAgents">重新加载</button>
      </p>

      <div v-if="loading && !items.length" class="feedback-skeleton" aria-label="正在加载反馈" aria-busy="true">
        <a-skeleton v-for="index in 3" :key="index" active :title="false" :paragraph="{ rows: 1 }" />
      </div>
      <ResourceEmptyState
        v-else-if="!items.length"
        size="compact"
        :icon="error ? AlertCircle : MessageSquare"
        :title="emptyTitle"
        :description="emptyDescription"
      >
        <template #actions>
          <a-button v-if="error" @click="loadPage()">重新加载</a-button>
          <a-button v-else-if="hasFilters" @click="clearFilters">清除筛选</a-button>
        </template>
      </ResourceEmptyState>
      <template v-else>
        <div class="feedback-table-scroll" tabindex="0" role="region" aria-label="反馈表格，可横向滚动">
          <table class="feedback-records" :aria-busy="loading" aria-label="智能体反馈记录">
            <colgroup>
              <col class="signal-column" />
              <col />
              <col class="agent-column" />
              <col class="time-column" />
              <col class="action-column" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">来源与评价</th>
                <th scope="col">反馈内容</th>
                <th scope="col">智能体</th>
                <th scope="col" aria-sort="descending">记录时间 <span aria-hidden="true">↓</span></th>
                <th scope="col" class="action-cell">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.id" class="feedback-record">
                <td>
                  <span class="feedback-signal" :class="signalClass(item)">
                    <component :is="signalIcon(item)" :size="14" aria-hidden="true" />
                    {{ signalLabel(item) }}
                  </span>
                </td>
                <td>
                  <p class="record-reason" :class="{ 'no-reason': !item.reason }">{{ item.reason || '无补充说明' }}</p>
                </td>
                <td>
                  <button type="button" class="record-agent" :title="`筛选智能体：${item.agent_name || item.agent_id}`" @click="selectAgent(item.agent_id)">
                    <FallbackAvatar kind="agent" :seed="item.agent_id" :size="24" decorative />
                    <span>{{ item.agent_name || item.agent_id }}</span>
                  </button>
                </td>
                <td>
                  <time class="record-time" :datetime="item.created_at">{{ formatDateTime(item.created_at) }}</time>
                </td>
                <td class="action-cell">
                  <button type="button" class="record-details text-action" :aria-label="`查看 ${item.agent_name || item.agent_id} 的${signalLabel(item)}详情`" @click="showDetails(item, $event)">
                    查看详情 <ChevronRight :size="14" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="error" class="page-error" role="alert">
          <AlertCircle :size="16" aria-hidden="true" />
          <span>{{ error }}</span>
          <a-button size="small" @click="loadPage(retryMore)">重新加载</a-button>
        </div>
        <footer class="list-footer">
          <a-button v-if="hasMore && !error" :loading="loading" @click="loadPage(true)">加载更多</a-button>
          <span v-else-if="!hasMore && !error">全部记录已加载</span>
        </footer>
      </template>
    </div>

    <a-modal
      v-model:open="detailOpen"
      title="反馈详情"
      :width="560"
      :destroy-on-close="true"
      :wrap-props="{ 'aria-modal': 'true' }"
      :body-style="{ maxHeight: '60vh', overflowY: 'auto' }"
      :after-close="() => detailTrigger?.focus()"
    >
      <template v-if="selected">
        <div class="detail-heading">
          <FallbackAvatar kind="agent" :seed="selected.agent_id" :size="36" decorative />
          <div>
            <strong>{{ selected.agent_name || selected.agent_id }}</strong>
            <span class="feedback-signal" :class="signalClass(selected)">
              <component :is="signalIcon(selected)" :size="14" aria-hidden="true" />
              {{ signalLabel(selected) }}
            </span>
          </div>
        </div>
        <section class="detail-section" aria-labelledby="feedback-content-label">
          <h3 id="feedback-content-label" ref="detailHeading" tabindex="-1">{{ selected.source === 'agent' ? '改进线索' : '补充说明' }}</h3>
          <p class="detail-reason" :class="{ 'no-reason': !selected.reason }">{{ selected.reason || '无补充说明' }}</p>
        </section>
        <section class="detail-section" aria-labelledby="feedback-context-label">
          <h3 id="feedback-context-label">关联执行</h3>
          <dl class="detail-metadata">
            <div><dt>记录时间</dt><dd>{{ formatDateTime(selected.created_at) }}</dd></div>
            <div v-if="selected.updated_at !== selected.created_at"><dt>更新时间</dt><dd>{{ formatDateTime(selected.updated_at) }}</dd></div>
          </dl>
        </section>
      </template>
      <template #footer>
        <p v-if="selected && traceError" class="session-error" role="alert">{{ traceError }}</p>
        <div class="detail-footer">
          <span>在 Langfuse 中查看本轮执行。</span>
          <a-button v-if="selected" :loading="openingTrace === selected.id" :disabled="!!openingTrace" @click="openTurnInLangfuse(selected)">
            打开 Langfuse Trace <ArrowUpRight :size="14" aria-hidden="true" />
          </a-button>
        </div>
      </template>
    </a-modal>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { AlertCircle, ArrowUpRight, ChevronRight, Lightbulb, MessageSquare, RefreshCw, ThumbsDown, ThumbsUp } from '@lucide/vue'
import { feedbackApi } from '@/apis/feedback_api'
import { formatDateTime } from '@/shared/lib/time'
import { resolveLangfuseTraceUrl } from '@/shared/lib/langfuse'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import ResourceEmptyState from '@/shared/ui/ResourceEmptyState.vue'

const sourceOptions = [{ value: '', label: '全部来源' }, { value: 'user', label: '用户评价' }, { value: 'agent', label: '智能体标记' }]
const ratingOptions = [{ value: '', label: '全部评价' }, { value: 'like', label: '正向评价' }, { value: 'dislike', label: '负向评价' }]
const filters = reactive({ source: '', rating: '', agent_id: '' })
const items = ref([])
const loading = ref(false)
const error = ref('')
const hasMore = ref(false)
const after = ref(null)
const retryMore = ref(false)
const agents = ref([])
const agentsLoading = ref(false)
const agentsError = ref(false)
const openingTrace = ref(null)
const traceError = ref('')
const selected = ref(null)
const detailOpen = ref(false)
const detailHeading = ref(null)
const hasFilters = computed(() => !!(filters.source || filters.rating || filters.agent_id))
const emptyTitle = computed(() => {
  if (error.value) return '反馈加载失败'
  return hasFilters.value ? '无匹配记录' : '暂无反馈记录'
})
const emptyDescription = computed(() => {
  if (error.value) return error.value
  if (hasFilters.value) return '调整筛选条件或清除筛选后查看。'
  return '用户评价与智能体标记提交后在此汇总。'
})
const agentOptions = computed(() => {
  const options = agents.value.map((agent) => ({ value: agent.agent_id, label: agent.name || agent.agent_id }))
  if (filters.agent_id && !options.some((option) => option.value === filters.agent_id)) {
    const name = items.value.find((item) => item.agent_id === filters.agent_id)?.agent_name
    options.push({ value: filters.agent_id, label: name || filters.agent_id })
  }
  return [{ value: '', label: '全部智能体' }, ...options]
})
let generation = 0
let detailTrigger = null
let detailViewRevision = 0
watch([detailOpen, detailHeading], ([open, heading]) => {
  if (open) heading?.focus()
  else detailViewRevision += 1
}, { flush: 'post' })

/** 离开列表时关闭详情，阻止迟到的观测查询接管导航。 */
function closeOnLeave() {
  detailViewRevision += 1
  detailOpen.value = false
  detailTrigger = null
}
onDeactivated(closeOnLeave)
onBeforeUnmount(closeOnLeave)

function signalLabel(item) {
  if (item.source === 'agent') return '智能体标记'
  return item.rating === 'like' ? '正向评价' : '负向评价'
}
function signalIcon(item) {
  if (item.source === 'agent') return Lightbulb
  return item.rating === 'like' ? ThumbsUp : ThumbsDown
}
function signalClass(item) {
  return item.source === 'agent' ? 'signal-agent' : `signal-${item.rating}`
}
function filterAgentOption(input, option) {
  return `${option.label} ${option.value}`.toLowerCase().includes(input.toLowerCase())
}
async function loadAgents() {
  agentsLoading.value = true
  agentsError.value = false
  try {
    agents.value = (await feedbackApi.listAgents()).agents
  } catch {
    agentsError.value = true
  } finally {
    agentsLoading.value = false
  }
}
async function loadPage(more = false) {
  if (more && loading.value) return
  const current = ++generation
  retryMore.value = more
  loading.value = true
  error.value = ''
  try {
    const page = await feedbackApi.list({ ...filters, after: more ? after.value : null })
    if (current !== generation) return
    items.value = more ? [...items.value, ...page.items] : page.items
    hasMore.value = page.has_more
    after.value = page.last_id
  } catch (cause) {
    if (current === generation) error.value = cause.message || '反馈加载失败，请重新加载'
  } finally {
    if (current === generation) loading.value = false
  }
}
function reload() {
  items.value = []
  hasMore.value = false
  return loadPage()
}
function changeSource() {
  filters.rating = ''
  return reload()
}
function selectAgent(value) {
  filters.agent_id = value || ''
  return reload()
}
function clearFilters() {
  Object.assign(filters, { source: '', rating: '', agent_id: '' })
  return reload()
}
function showDetails(item, event) {
  detailViewRevision += 1
  detailTrigger = event.currentTarget
  selected.value = item
  traceError.value = ''
  detailOpen.value = true
}
async function openTurnInLangfuse(item) {
  if (openingTrace.value) return
  const targetWindow = window.open('about:blank', '_blank')
  if (!targetWindow) {
    traceError.value = '浏览器阻止了新标签页，请允许弹窗后重试'
    return
  }
  targetWindow.opener = null
  const revision = detailViewRevision
  openingTrace.value = item.id
  traceError.value = ''
  try {
    const result = await feedbackApi.getTurnLangfuse(item.id)
    if (revision !== detailViewRevision) {
      targetWindow.close()
      return
    }
    const url = resolveLangfuseTraceUrl(result)
    if (!url) {
      targetWindow.close()
      traceError.value = result.langfuse_trace_id ? 'Langfuse 当前不可用，请检查配置或稍后重试' : '本轮暂无可用的 Langfuse Trace'
      return
    }
    targetWindow.location.replace(url)
  } catch (cause) {
    targetWindow.close()
    if (revision === detailViewRevision) traceError.value = cause.status === 404 ? '反馈已不可用，请刷新列表' : (cause.message || '获取 Langfuse 地址失败，请重试')
  } finally {
    openingTrace.value = null
  }
}
onMounted(() => { void reload(); void loadAgents() })
</script>

<style scoped lang="less">
.feedback-list { padding: var(--page-padding); }
.feedback-panel { border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); overflow: hidden; }
.feedback-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 20px; padding: 12px 16px; }
.filter-field {
  display: flex;
  align-items: center;
  gap: 8px;
  > span, > label { flex-shrink: 0; font-size: 12px; color: var(--gray-600); }
}
.source-field :deep(.ant-select) { width: 132px; }
.agent-field :deep(.ant-select) { width: 180px; }
.rating-field :deep(.ant-select) { width: 116px; }
.toolbar-actions { display: flex; align-items: center; gap: 12px; margin-left: auto; }
.feedback-count { color: var(--gray-500); font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.agent-load-error { margin: 0 16px 12px; color: var(--color-error-700); font-size: 13px; }
.feedback-table-scroll { overflow-x: auto; border-top: 1px solid var(--gray-150); }
.feedback-table-scroll:focus-visible { outline: 2px solid var(--main-color); outline-offset: -2px; }
.feedback-records {
  width: 100%;
  min-width: 720px;
  border-collapse: collapse;
  table-layout: fixed;
  .signal-column { width: 128px; }
  .agent-column { width: 164px; }
  .time-column { width: 148px; }
  .action-column { width: 112px; }
  th, td { padding: 10px 16px; text-align: left; vertical-align: middle; }
  th { height: 40px; background: var(--gray-25); color: var(--gray-600); font-size: 12px; font-weight: 500; white-space: nowrap; }
  td { border-top: 1px solid var(--gray-150); }
  .action-cell { text-align: right; }
}
.feedback-record {
  height: 64px;
  &:hover { background: var(--gray-25); }
}
.feedback-signal {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-radius: 6px;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
  &.signal-agent { background: var(--main-50); color: var(--main-700); }
  &.signal-like { background: var(--color-success-50); color: var(--color-success-900); }
  &.signal-dislike { background: var(--color-warning-50); color: var(--color-warning-900); }
}
.record-reason {
  margin: 0;
  color: var(--gray-800);
  font-size: 13px;
  line-height: 20px;
  white-space: normal;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  max-height: 40px;
  overflow: hidden;
}
.no-reason { color: var(--gray-600); }
.record-agent {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  width: 100%;
  min-height: 28px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--gray-700);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { color: var(--main-700); }
}
.record-details { white-space: nowrap; }
.record-time { white-space: nowrap; font-size: 12px; color: var(--gray-600); line-height: 28px; font-variant-numeric: tabular-nums; }
.text-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 0;
  min-height: 28px;
  border: 0;
  background: transparent;
  color: var(--main-700);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  &:hover { text-decoration: underline; }
}
.text-action:focus-visible, .record-agent:focus-visible { outline: 2px solid var(--main-color); outline-offset: 3px; border-radius: 4px; }
.list-footer { display: flex; justify-content: center; align-items: center; min-height: 44px; padding: 8px 16px; border-top: 1px solid var(--gray-150); color: var(--gray-600); font-size: 12px; }
.page-error { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 16px 20px; padding: 12px; border-radius: 6px; background: var(--color-error-50); color: var(--color-error-700); font-size: 13px; span { flex: 1; } }
.feedback-skeleton { padding: 0 20px; :deep(.ant-skeleton) { padding: 20px 0; border-top: 1px solid var(--gray-150); } }
.detail-heading { display: flex; align-items: center; gap: 12px; margin-bottom: 28px; > div { display: grid; gap: 8px; min-width: 0; } strong { color: var(--gray-900); font-size: 16px; overflow-wrap: anywhere; } }
.detail-section { margin-top: 24px; h3 { margin: 0 0 12px; color: var(--gray-900); font-size: 13px; font-weight: 600; } }
.detail-reason { margin: 0; color: var(--gray-800); font-size: 14px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.detail-metadata { margin: 0; > div { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 16px; padding: 8px 0; font-size: 12px; line-height: 1.6; } dt { color: var(--gray-600); } dd { margin: 0; color: var(--gray-800); overflow-wrap: anywhere; } }
.detail-footer { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; span { color: var(--gray-600); font-size: 12px; } }
.session-error { margin: 0 0 12px; color: var(--color-error-700); font-size: 13px; }
@media (prefers-reduced-motion: reduce) { .feedback-skeleton :deep(.ant-skeleton-content .ant-skeleton-title), .feedback-skeleton :deep(.ant-skeleton-content .ant-skeleton-paragraph > li) { animation: none; } }
</style>
