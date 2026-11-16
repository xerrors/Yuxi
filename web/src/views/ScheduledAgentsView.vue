<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { ExternalLink, Pause, Play, Plus, RefreshCw, Trash2, X, Zap } from '@lucide/vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

import { scheduledAgentApi } from '@/apis/scheduled_agent_api'
import ScheduledAgentEditor from '@/components/scheduled-agents/ScheduledAgentEditor.vue'
import {
  createRetriableRequestIds,
  createScheduledAgentAutosave
} from '@/components/scheduled-agents/scheduledAgentAutosave'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import { useAgentStore } from '@/stores/agent'
import { describeSchedule, parseCronExpression } from '@/utils/scheduleFrequency'

const router = useRouter()
const agentStore = useAgentStore()
const jobs = ref([])
const loading = ref(false)
const saving = ref(false)
const listError = ref('')
const editorError = ref('')
const saveState = ref('idle')
const selectedJobId = ref('')
const creatingDraft = ref(false)
const activeActionId = ref('')
const searchQuery = ref('')
const statusFilter = ref('all')
const runNowRequests = createRetriableRequestIds()
const ACCEPTED_RUN_NOW_STATUSES = new Set([
  'dispatching',
  'submitted',
  'queued',
  'dispatched',
  'pending',
  'running'
])
const RUN_STATUS_LABELS = {
  dispatching: '提交中',
  submitted: '已提交',
  queued: '排队中',
  dispatched: '已派发',
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  skipped: '已跳过',
  failed: '失败',
  rejected: '已拒绝',
  cancelled: '已取消',
  interrupted: '已中断'
}

const availableAgents = computed(() =>
  (agentStore.agents || []).filter((agent) => !agent.is_subagent)
)
const selectedJob = computed(() => jobs.value.find((job) => job.id === selectedJobId.value) || null)
const detailOpen = computed(() => creatingDraft.value || Boolean(selectedJob.value))
const detailStatusLabel = computed(() => {
  if (creatingDraft.value) return '新任务'
  return selectedJob.value?.enabled ? '已开启' : '已暂停'
})

const filteredJobs = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  return jobs.value.filter((job) => {
    if (statusFilter.value === 'enabled' && !job.enabled) return false
    if (statusFilter.value === 'paused' && job.enabled) return false
    if (!query) return true
    return [job.name, job.prompt, agentLabel(job), scheduleLabel(job)]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase().includes(query))
  })
})

const filterOptions = computed(() => [
  { value: 'all', label: '全部', count: jobs.value.length },
  { value: 'enabled', label: '已开启', count: jobs.value.filter((job) => job.enabled).length },
  { value: 'paused', label: '已暂停', count: jobs.value.filter((job) => !job.enabled).length }
])

async function load({ silent = false } = {}) {
  if (!silent) loading.value = true
  listError.value = ''
  try {
    jobs.value = (await scheduledAgentApi.list()).jobs || []
    if (selectedJobId.value && !jobs.value.some((job) => job.id === selectedJobId.value)) {
      selectedJobId.value = ''
    }
  } catch (error) {
    listError.value = error.message || '加载定时任务失败'
  } finally {
    if (!silent) loading.value = false
  }
}

function scheduleLabel(job) {
  return describeSchedule(parseCronExpression(job.cron_expression))
}

function agentLabel(job) {
  return (
    availableAgents.value.find((agent) => (agent.slug || agent.id) === job.agent_slug)?.name ||
    job.agent_slug
  )
}

const autosave = createScheduledAgentAutosave({
  persist: ({ jobId, payload, requestId }) =>
    jobId
      ? scheduledAgentApi.update(jobId, payload)
      : scheduledAgentApi.create({ ...payload, request_id: requestId }),
  onPersisted(savedJob, { created, finalizeDraft }) {
    if (created) jobs.value = [{ ...savedJob, runs: [] }, ...jobs.value]
    else {
      jobs.value = jobs.value.map((job) =>
        job.id === savedJob.id ? { ...job, ...savedJob, runs: job.runs || [] } : job
      )
    }
    if (finalizeDraft) {
      creatingDraft.value = false
      selectedJobId.value = savedJob.id
    }
  },
  onState(next) {
    saveState.value = next.state
    saving.value = next.saving
    editorError.value = next.error
  }
})

function flushAutoSave() {
  return autosave.flush()
}

function queueAutoSave(change) {
  autosave.queue(change, selectedJob.value?.id)
}

async function selectJob(job) {
  if (!(await flushAutoSave())) return
  creatingDraft.value = false
  selectedJobId.value = job.id
  autosave.leaveEditor()
}

async function openCreate() {
  if (!(await flushAutoSave())) return
  autosave.beginDraft()
  creatingDraft.value = true
  selectedJobId.value = ''
}

async function closeDetail() {
  const discardingInvalidDraft = creatingDraft.value && autosave.canDiscardInvalidDraft()
  if (!discardingInvalidDraft && !(await flushAutoSave())) return
  creatingDraft.value = false
  selectedJobId.value = ''
  autosave.leaveEditor()
}

async function runAction(job, action, fallback) {
  if (!(await flushAutoSave())) return null
  activeActionId.value = job.id
  try {
    const result = await action()
    await load({ silent: true })
    return result
  } catch (error) {
    message.error(error.message || fallback)
    return null
  } finally {
    activeActionId.value = ''
  }
}

function toggle(job) {
  return runAction(
    job,
    () => scheduledAgentApi.update(job.id, { enabled: !job.enabled }),
    '更新任务状态失败'
  )
}

async function runNow(job) {
  if (!(await flushAutoSave())) return
  activeActionId.value = job.id
  const requestId = runNowRequests.get(job.id)
  try {
    const run = await scheduledAgentApi.runNow(job.id, requestId)
    runNowRequests.complete(job.id)
    await load({ silent: true })
    if (ACCEPTED_RUN_NOW_STATUSES.has(run.status)) {
      message.success('已创建一次立即运行')
    } else if (run.status === 'skipped') {
      message.warning(run.error_message || '本次运行已跳过')
    } else {
      message.error(run.error_message || `立即运行状态：${run.status || '未知'}`)
    }
  } catch (error) {
    message.error(error.message || '立即运行失败')
  } finally {
    activeActionId.value = ''
  }
}

function remove(job) {
  Modal.confirm({
    title: '删除定时任务？',
    content: '删除只会停止未来触发，不会删除已经产生的 AgentRun。',
    okText: '删除',
    cancelText: '取消',
    okType: 'danger',
    async onOk() {
      const removed = await runAction(
        job,
        () => scheduledAgentApi.remove(job.id),
        '删除定时任务失败'
      )
      if (removed !== null) await closeDetail()
    }
  })
}

function formatRunTime(value) {
  if (!value) return '等待调度'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function runStatusLabel(run) {
  return RUN_STATUS_LABELS[run.status] || run.status
}

function runStatusTone(run) {
  if (run.status === 'completed') return 'success'
  if (['failed', 'rejected', 'cancelled', 'interrupted'].includes(run.status)) return 'danger'
  if (run.status === 'skipped') return 'warning'
  return 'active'
}

function canOpenConversation(run) {
  return Boolean(run.conversation_available && run.thread_id)
}

async function openConversation(run) {
  if (!canOpenConversation(run)) return
  if (!(await flushAutoSave())) return
  await router.push({ name: 'AgentCompWithThreadId', params: { thread_id: run.thread_id } })
}

function runRecordLabel(run) {
  const trigger = run.trigger === 'manual' ? '手动运行' : '定时运行'
  const availability = canOpenConversation(run) ? '打开对应对话' : '没有可用对话'
  return `${trigger}，${runStatusLabel(run)}，${formatRunTime(run.scheduled_for)}，${availability}`
}

watch([searchQuery, statusFilter], () => {
  if (!selectedJob.value) return
  if (!filteredJobs.value.some((job) => job.id === selectedJob.value.id)) void closeDetail()
})

onMounted(async () => {
  if (!agentStore.isInitialized) await agentStore.initialize()
  await load()
})

onBeforeRouteLeave(() => flushAutoSave())

defineExpose({ beforeLeave: flushAutoSave, loading, saving })
</script>

<template>
  <div class="scheduled-view">
    <section class="scheduled-shell" :class="{ open: detailOpen }">
      <aside class="list-pane" aria-label="定时任务列表">
        <PageShoulder
          v-model:search="searchQuery"
          class="schedule-shoulder"
          search-placeholder="搜索已安排任务"
        >
          <template #actions>
            <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
              <Plus :size="14" />
              新建任务
            </a-button>
            <a-button
              class="lucide-icon-btn"
              :loading="loading"
              aria-label="刷新定时任务"
              @click="load()"
            >
              <RefreshCw :size="14" />
            </a-button>
          </template>
        </PageShoulder>

        <a-alert v-if="listError" class="list-alert" type="error" show-icon :message="listError">
          <template #action><a-button size="small" @click="load()">重新加载</a-button></template>
        </a-alert>

        <template v-else>
          <div class="filter-row" aria-label="任务状态筛选">
            <button
              v-for="option in filterOptions"
              :key="option.value"
              type="button"
              :class="{ active: statusFilter === option.value }"
              :aria-pressed="statusFilter === option.value"
              @click="statusFilter = option.value"
            >
              {{ option.label }} <span>{{ option.count }}</span>
            </button>
          </div>

          <div v-if="loading" class="task-skeleton" aria-label="正在加载定时任务">
            <span v-for="index in 3" :key="index"></span>
          </div>

          <div v-else-if="filteredJobs.length" class="task-list">
            <button
              v-for="job in filteredJobs"
              :key="job.id"
              type="button"
              class="task-row"
              :class="{ selected: selectedJobId === job.id }"
              :aria-current="selectedJobId === job.id ? 'true' : undefined"
              @click="selectJob(job)"
            >
              <span class="task-copy">
                <strong>{{ job.name }}</strong>
                <small>{{ scheduleLabel(job) }}</small>
              </span>
              <span class="task-state">{{ job.enabled ? '开启' : '暂停' }}</span>
            </button>
          </div>

          <div v-else class="list-empty">
            <strong>{{ jobs.length ? '没有匹配的任务' : '还没有定时任务' }}</strong>
            <span>{{ jobs.length ? '调整搜索或筛选条件。' : '使用右上角的新建任务开始。' }}</span>
          </div>
        </template>
      </aside>

      <Transition name="detail-slide">
        <main v-if="detailOpen" class="detail-pane">
          <header class="detail-toolbar">
            <span class="task-status" :class="{ paused: selectedJob && !selectedJob.enabled }">
              {{ detailStatusLabel }}
            </span>
            <div class="detail-actions">
              <template v-if="selectedJob">
                <button
                  type="button"
                  :disabled="Boolean(activeActionId)"
                  @click="runNow(selectedJob)"
                >
                  <Zap :size="15" aria-hidden="true" />
                  立即运行
                </button>
                <button
                  type="button"
                  :disabled="Boolean(activeActionId)"
                  @click="toggle(selectedJob)"
                >
                  <Pause v-if="selectedJob.enabled" :size="15" aria-hidden="true" />
                  <Play v-else :size="15" aria-hidden="true" />
                  {{ selectedJob.enabled ? '暂停' : '恢复' }}
                </button>
                <button
                  type="button"
                  class="icon-button danger"
                  aria-label="删除任务"
                  :disabled="Boolean(activeActionId)"
                  @click="remove(selectedJob)"
                >
                  <Trash2 :size="15" aria-hidden="true" />
                </button>
              </template>
              <button
                type="button"
                class="icon-button"
                aria-label="关闭任务详情"
                @click="closeDetail"
              >
                <X :size="17" aria-hidden="true" />
              </button>
            </div>
          </header>

          <ScheduledAgentEditor
            :job="selectedJob"
            :agents="availableAgents"
            :saving="saving"
            :save-state="saveState"
            :error="editorError"
            @change="queueAutoSave"
          />

          <section v-if="selectedJob" class="history-section" aria-labelledby="runs-heading">
            <header>
              <div>
                <h3 id="runs-heading">运行历史记录</h3>
                <p>点击记录进入本次运行创建的对话。</p>
              </div>
              <span>{{ selectedJob.runs?.length || 0 }} 条</span>
            </header>

            <div v-if="selectedJob.runs?.length" class="run-list">
              <button
                v-for="run in selectedJob.runs"
                :key="run.id"
                type="button"
                class="run-row"
                :class="{ actionable: canOpenConversation(run) }"
                :disabled="!canOpenConversation(run)"
                :aria-label="runRecordLabel(run)"
                @click="openConversation(run)"
              >
                <span class="run-status" :class="runStatusTone(run)">{{
                  runStatusLabel(run)
                }}</span>
                <span class="run-copy">
                  <strong>{{ run.trigger === 'manual' ? '手动运行' : '定时运行' }}</strong>
                  <small v-if="run.error_message" :title="run.error_message">{{
                    run.error_message
                  }}</small>
                  <small v-else>{{
                    canOpenConversation(run) ? '查看对话和运行结果' : '尚未创建对话'
                  }}</small>
                </span>
                <time :datetime="run.scheduled_for">{{ formatRunTime(run.scheduled_for) }}</time>
                <ExternalLink v-if="canOpenConversation(run)" :size="15" aria-hidden="true" />
                <span v-else class="no-conversation">无对话</span>
              </button>
            </div>
            <p v-else class="runs-empty">任务运行后，记录会显示在这里。</p>
          </section>
        </main>
      </Transition>
    </section>
  </div>
</template>

<style lang="less" scoped>
.scheduled-view {
  display: flex;
  width: 100%;
  height: 100%;
  min-height: 0;
  flex-direction: column;
}

.schedule-shoulder {
  width: 100%;
  max-width: 820px;
  margin: 0 auto;
  flex: none;

  :deep(.page-shoulder-left) {
    min-width: 0;
    flex: 1;
  }

  :deep(.search-input) {
    width: min(280px, 100%);
  }
}

.scheduled-shell {
  display: flex;
  flex: 1;
  width: 100%;
  min-height: 0;
  overflow: hidden;
  color: var(--gray-1000);
}

.list-alert {
  flex: 1;
  margin: 20px;
}

.list-pane {
  display: flex;
  flex-direction: column;
  flex: 1 1 100%;
  min-width: 0;
  height: 100%;
  overflow-y: auto;
  border-right: 1px solid transparent;
  transition:
    flex-basis 160ms cubic-bezier(0.16, 1, 0.3, 1),
    max-width 160ms cubic-bezier(0.16, 1, 0.3, 1),
    border-color 160ms ease,
    background-color 160ms ease;
}

.scheduled-shell:not(.open) .list-pane {
  max-width: 820px;
  margin: 0 auto;
  padding-bottom: 24px;
}

.scheduled-shell.open .list-pane {
  flex: 0 0 40%;
  max-width: 40%;
  border-right-color: var(--gray-150);
}

.filter-row {
  position: sticky;
  z-index: 1;
  top: 0;
  padding: 10px 14px 12px;
  background: inherit;
  display: flex;
  align-items: center;
  gap: 4px;

  button {
    display: inline-flex;
    min-height: 28px;
    padding: 0 8px;
    border: 0;
    border-radius: 5px;
    background: transparent;
    color: var(--gray-500);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    align-items: center;
    gap: 5px;

    &:hover,
    &.active {
      background: var(--gray-50);
      color: var(--gray-900);
    }

    span {
      color: var(--gray-400);
      font-variant-numeric: tabular-nums;
    }
  }
}

.task-list {
  display: grid;
  padding: 8px 12px;
  gap: 4px;
}

.task-row {
  display: grid;
  width: 100%;
  min-height: 60px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  column-gap: 10px;
  transition:
    background-color 120ms ease,
    border-color 120ms ease;

  &:hover,
  &.selected {
    border-color: var(--gray-150);
    background: var(--gray-0);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: -2px;
  }
}

.task-copy {
  display: grid;
  min-width: 0;
  gap: 3px;

  strong,
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 550;
  }

  small {
    color: var(--gray-400);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
}

.task-state {
  color: var(--gray-400);
  font-size: 11px;
}

.task-skeleton {
  display: grid;
  padding: 8px 12px;
  gap: 4px;

  span {
    height: 62px;
    border-radius: 9px;
    background: var(--gray-25);
  }
}

.list-empty {
  display: grid;
  min-height: 240px;
  color: var(--gray-400);
  place-content: center;
  justify-items: center;
  gap: 5px;

  strong {
    color: var(--gray-700);
    font-size: 14px;
  }

  span {
    font-size: 12px;
  }
}

.detail-pane {
  flex: 0 0 60%;
  width: 60%;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  background: var(--gray-0);
}

.detail-toolbar {
  display: flex;
  min-height: 48px;
  padding: 0 16px 0 22px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.task-status {
  color: var(--color-success-700);
  font-size: 12px;

  &.paused {
    color: var(--gray-500);
  }
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: 4px;

  button {
    display: inline-flex;
    height: 30px;
    padding: 0 8px;
    border: 1px solid transparent;
    border-radius: 5px;
    background: transparent;
    color: var(--gray-700);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    align-items: center;
    justify-content: center;
    gap: 4px;

    svg {
      flex: none;
    }

    &:hover:not(:disabled) {
      background: var(--gray-50);
      color: var(--gray-1000);
    }

    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: 1px;
    }

    &:disabled {
      cursor: default;
      opacity: 0.45;
    }
  }

  .icon-button {
    width: 30px;
    padding: 0;
  }

  .danger:hover:not(:disabled) {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.history-section {
  padding: 0 22px 24px;

  > header {
    display: flex;
    padding: 10px 0 6px;
    border-bottom: 1px solid var(--gray-150);
    align-items: flex-end;
    justify-content: space-between;

    h3 {
      margin: 0;
      color: var(--gray-700);
      font-size: 12px;
      font-weight: 500;
    }

    p {
      margin: 2px 0 0;
      color: var(--gray-400);
      font-size: 11px;
    }

    > span {
      color: var(--gray-400);
      font-size: 11px;
    }
  }
}

.run-list {
  border-bottom: 1px solid var(--gray-100);
}

.run-row {
  display: grid;
  width: 100%;
  min-height: 44px;
  padding: 6px 0;
  border: 0;
  border-bottom: 1px solid var(--gray-100);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  grid-template-columns: 58px minmax(0, 1fr) auto 18px;
  align-items: center;
  gap: 10px;

  &:last-child {
    border-bottom: 0;
  }

  &.actionable {
    cursor: pointer;

    &:hover {
      background: var(--gray-25);
    }
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  time,
  svg,
  .no-conversation {
    color: var(--gray-400);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
}

.run-status {
  color: var(--color-info-700);
  font-size: 11px;

  &.success {
    color: var(--color-success-700);
  }

  &.danger {
    color: var(--color-error-700);
  }

  &.warning {
    color: var(--color-warning-800);
  }
}

.run-copy {
  display: grid;
  min-width: 0;
  gap: 2px;

  strong,
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: var(--gray-800);
    font-size: 12px;
    font-weight: 550;
  }

  small {
    color: var(--gray-400);
    font-size: 11px;
  }
}

.runs-empty {
  margin: 0;
  padding: 22px 0;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-400);
  font-size: 12px;
}

.detail-slide-enter-active {
  transition:
    flex-basis 160ms cubic-bezier(0.16, 1, 0.3, 1),
    width 160ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 130ms ease,
    transform 160ms cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
  min-width: 0 !important;
}

.detail-slide-leave-active {
  transition:
    flex-basis 140ms cubic-bezier(0.16, 1, 0.3, 1),
    width 140ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 110ms ease,
    transform 140ms cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
  min-width: 0 !important;
}

.detail-slide-enter-from,
.detail-slide-leave-to {
  flex-basis: 0 !important;
  width: 0 !important;
  min-width: 0 !important;
  opacity: 0;
  transform: translateX(14px);
}

@media (max-width: 1100px) {
  .scheduled-shell.open {
    display: block;
  }

  .open .list-pane {
    display: none;
  }

  .scheduled-shell.open .detail-pane {
    width: 100%;
    height: 100%;
    flex: 1 1 100%;
  }
}

@media (max-width: 640px) {
  .schedule-shoulder {
    gap: 8px;
    padding-inline: 12px;
  }

  .scheduled-shell:not(.open) .list-pane {
    padding-inline: 12px;
  }

  .detail-toolbar {
    padding-inline: 12px;
  }

  .detail-actions button:not(.icon-button) {
    width: 30px;
    padding: 0;
    font-size: 0;
    gap: 0;
  }

  .history-section {
    padding-inline: 16px;
  }

  .run-row {
    grid-template-columns: 52px minmax(0, 1fr) 18px;

    time,
    .no-conversation {
      display: none;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .scheduled-shell,
  .list-pane {
    transition: none;
  }

  .detail-slide-enter-active,
  .detail-slide-leave-active {
    transition: none;
  }

  .detail-slide-enter-from,
  .detail-slide-leave-to {
    transform: none;
  }
}
</style>
