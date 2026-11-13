<template>
  <div class="message-debug-panel">
    <header class="debug-toolbar">
      <a-select
        v-model:value="currentFilter"
        class="filter-select-field"
        size="small"
        aria-label="记录类型筛选"
      >
        <a-select-option v-for="option in filterTabs" :key="option.key" :value="option.key">
          {{ option.label }} {{ option.count }}
        </a-select-option>
      </a-select>
      <span
        v-if="traceDataTruncated"
        class="truncated-note"
        aria-label="仅展示最新 500 条调试记录"
        title="更早的审计或运行记录未包含在当前响应中"
      >
        <TriangleAlert :size="12" aria-hidden="true" />
        <span class="truncated-label">最新 500 条</span>
      </span>
      <label class="search-field">
        <Search :size="13" aria-hidden="true" />
        <span class="sr-only">搜索消息或字段</span>
        <input v-model="searchQuery" type="search" placeholder="搜索消息或字段" />
      </label>
      <div class="toolbar-actions">
        <button
          type="button"
          class="icon-button"
          :class="{ active: isTraceOverviewVisible }"
          :disabled="timelineItems.length === 0"
          :aria-pressed="isTraceOverviewVisible"
          :aria-label="isTraceOverviewVisible ? '隐藏时间概览' : '显示时间概览'"
          :title="isTraceOverviewVisible ? '隐藏时间概览' : '显示时间概览'"
          @click="toggleTraceOverview"
        >
          <Clock :size="14" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-button"
          :disabled="isAuditLoading || !threadId"
          :aria-busy="isAuditLoading"
          :aria-label="isAuditLoading ? '正在读取审计' : '刷新审计'"
          :title="isAuditLoading ? '正在读取审计' : '刷新审计'"
          @click="refreshMessageAudits"
        >
          <LoaderCircle v-if="isAuditLoading" :size="14" class="spinning" aria-hidden="true" />
          <RefreshCw v-else :size="14" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-button"
          :aria-label="isAllCopied ? '已复制全部数据' : '复制全部数据'"
          :title="isAllCopied ? '已复制全部数据' : '复制全部数据'"
          @click="copyAllTimelineJson"
        >
          <Check v-if="isAllCopied" :size="14" class="success-icon" aria-hidden="true" />
          <Copy v-else :size="14" aria-hidden="true" />
        </button>
      </div>
    </header>

    <div v-if="auditLoadError" class="audit-error" role="alert">
      <TriangleAlert :size="14" aria-hidden="true" />
      <span>数据库记录读取失败，当前保留上次读取的数据。</span>
      <button type="button" @click="refreshMessageAudits">重试</button>
    </div>

    <section
      v-if="isTraceOverviewVisible && combinedTrace"
      class="trace-overview"
      aria-labelledby="trace-overview-title"
    >
      <div class="trace-overview-header">
        <span id="trace-overview-title">时间概览</span>
        <div class="range-summary">
          <span>{{ timelineRangeLabel }}</span>
          <button
            v-if="isTimelineRangeActive"
            type="button"
            class="reset-range-button"
            @click="resetTimelineRange"
          >
            重置范围
          </button>
        </div>
      </div>

      <div class="trace-axis" aria-hidden="true">
        <span
          v-for="tick in timelineAxisTicks"
          :key="tick.ratio"
          class="axis-tick"
          :style="{ left: `${tick.ratio * 100}%` }"
        >
          {{ tick.label }}
        </span>
      </div>

      <div
        class="trace-track"
        :class="{ 'range-active': isTimelineRangeActive }"
        :aria-label="timelineRangeAriaLabel"
        @click="selectTimelineWindowAtPointer"
      >
        <div class="track-grid" aria-hidden="true"></div>
        <div class="phase-lane" aria-hidden="true">
          <span
            v-for="phase in combinedTrace.phases"
            :key="phase.key"
            class="phase-segment"
            :class="[
              `phase-${phase.type}`,
              { 'timeline-mark-selected': isTimelineMarkSelected(phase) }
            ]"
            :style="{ left: `${phase.leftPercent}%`, width: `${phase.widthPercent}%` }"
            :title="`${phase.label}：${formatRunTimingDuration(phase.endOffsetMs - phase.startOffsetMs)}`"
          ></span>
        </div>
        <div class="operation-lane" aria-hidden="true">
          <span
            v-for="span in combinedTrace.spans"
            :key="span.key"
            class="operation-span"
            :class="[
              `span-${span.role}`,
              {
                'span-error': isFailureStatus(span.executionStatus) || span.role === 'error',
                'span-timing-fallback': span.timingFallback,
                'timeline-mark-selected': isTimelineMarkSelected(span)
              }
            ]"
            :style="{ left: `${span.leftPercent}%`, width: `${span.widthPercent}%` }"
            :title="traceSpanTitle(span)"
          ></span>
        </div>
        <span
          class="range-mask range-mask-start"
          :style="{ width: `${timelineRangeStartPercent}%` }"
          aria-hidden="true"
        ></span>
        <span
          class="range-mask range-mask-end"
          :style="{ width: `${100 - timelineRangeEndPercent}%` }"
          aria-hidden="true"
        ></span>
        <span
          class="selected-window"
          :style="{
            left: `${timelineRangeStartPercent}%`,
            width: `${timelineRangeEndPercent - timelineRangeStartPercent}%`
          }"
          aria-hidden="true"
        ></span>
        <input
          class="range-input range-input-start"
          type="range"
          min="0"
          max="1000"
          step="1"
          :value="timelineRangeStart"
          aria-label="时间范围起点"
          @input="updateTimelineRangeStart"
        />
        <input
          class="range-input range-input-end"
          type="range"
          min="0"
          max="1000"
          step="1"
          :value="timelineRangeEnd"
          aria-label="时间范围终点"
          @input="updateTimelineRangeEnd"
        />
      </div>
      <div class="trace-caption">
        <span>点击选择 2s，拖动手柄微调</span>
        <span>仅累计执行时间，已移除运行间隔</span>
      </div>
    </section>

    <section
      v-else-if="isTraceOverviewVisible && timelineItems.length"
      class="trace-unavailable"
      aria-label="时间概览不可用"
    >
      <Clock :size="13" aria-hidden="true" />
      <span>运行时间尚未就绪，发送时间可在下方列表查看。</span>
    </section>

    <div
      ref="traceWorkbenchRef"
      class="trace-workbench"
      :class="{ 'has-inspector': Boolean(selectedTarget) }"
      :style="traceWorkbenchStyle"
    >
      <section class="records-pane" aria-label="调试记录">
        <div class="record-table-header" aria-hidden="true">
          <span>类型</span>
          <span>记录</span>
          <span
            class="column-time"
            title="统一按上海时区显示。Turn 行显示轮次创建时间；消息和工具行依次取结束、开始、记录时间。"
          >
            时间（上海）
          </span>
          <span class="column-duration">耗时</span>
          <span class="column-tokens">Tokens</span>
          <span></span>
        </div>

        <div v-if="visibleItemCount === 0" class="empty-records">
          <LoaderCircle v-if="isAuditLoading" :size="22" class="spinning" aria-hidden="true" />
          <Clock v-else :size="22" aria-hidden="true" />
          <span>{{ emptyTimelineText }}</span>
        </div>

        <div v-else class="record-list">
          <template v-for="group in filteredTimelineGroups" :key="group.key">
            <button
              type="button"
              class="record-row run-row"
              :class="{ selected: selectedTargetKey === turnTargetKey(group) }"
              :aria-label="turnRowAriaLabel(group)"
              @click="selectTurn(group)"
            >
              <span class="record-kind kind-run">{{ group.turnId ? '轮次' : '输入' }}</span>
              <span class="record-summary run-summary">
                <strong>{{ turnTitle(group) }}</strong>
                <span>{{ turnSummary(group) }}</span>
              </span>
              <time class="record-cell column-time">{{ formatTurnTime(group) }}</time>
              <span class="record-cell column-duration">{{ formatTurnDuration(group) }}</span>
              <span class="record-cell column-tokens"></span>
              <ChevronRight :size="14" class="record-chevron" aria-hidden="true" />
            </button>

            <button
              v-for="item in group.visibleItems"
              :key="item.id"
              type="button"
              class="record-row message-row"
              :class="[
                `role-${item.role}`,
                { selected: selectedTargetKey === itemTargetKey(group, item) }
              ]"
              :aria-label="itemRowAriaLabel(item)"
              @click="selectItem(group, item)"
            >
              <span class="record-kind" :class="`kind-${item.role}`">{{
                roleNames[item.role] || '其他'
              }}</span>
              <span class="record-summary" :title="item.displaySummary">{{
                item.displaySummary
              }}</span>
              <time class="record-cell column-time" :title="formatAuditTimeTooltip(item)">
                {{ formatRecordTime(item) }}
              </time>
              <span
                class="record-cell column-duration"
                title="后端 monotonic clock 记录的 Model/Tool 耗时"
              >
                {{ formatAuditDuration(item.durationMs) }}
              </span>
              <span class="record-cell column-tokens" :title="item.tokenTooltip">
                {{ item.tokenSummary }}
              </span>
              <ChevronRight :size="14" class="record-chevron" aria-hidden="true" />
            </button>
          </template>
        </div>
      </section>

      <div
        v-if="selectedTarget"
        class="inspector-resizer"
        role="separator"
        :aria-label="isWideInspectorLayout ? '调整记录详情宽度' : '调整记录详情高度'"
        :aria-orientation="isWideInspectorLayout ? 'vertical' : 'horizontal'"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="inspectorSizePercent"
        :aria-valuetext="`详情区占 ${inspectorSizePercent}%`"
        tabindex="0"
        @pointerdown="startInspectorResize"
        @pointermove="moveInspectorResize"
        @pointerup="stopInspectorResize"
        @pointercancel="stopInspectorResize"
        @keydown="resizeInspectorWithKeyboard"
      >
        <span aria-hidden="true"></span>
      </div>

      <aside v-if="selectedTarget" class="record-inspector" aria-label="记录详情">
        <header class="inspector-toolbar">
          <nav class="inspector-tabs" aria-label="详情视图">
            <button
              type="button"
              :class="{ active: inspectorTab === 'overview' }"
              :aria-pressed="inspectorTab === 'overview'"
              @click="inspectorTab = 'overview'"
            >
              概览
            </button>
            <button
              type="button"
              :class="{ active: inspectorTab === 'data' }"
              :aria-pressed="inspectorTab === 'data'"
              @click="inspectorTab = 'data'"
            >
              数据
            </button>
          </nav>
          <div class="inspector-actions">
            <a-dropdown v-model:open="isExportMenuOpen" :trigger="['click']" placement="bottomRight">
              <button
                type="button"
                class="icon-button"
                aria-label="Turn 导出选项"
                title="Turn 导出选项"
                aria-haspopup="menu"
                :aria-expanded="isExportMenuOpen"
                :disabled="isExporting"
                :aria-busy="isExporting"
              >
                <LoaderCircle v-if="isExporting" :size="14" class="spinning" aria-hidden="true" />
                <Ellipsis v-else :size="14" aria-hidden="true" />
              </button>
              <template #overlay>
                <a-menu @click="exportSelectedTurn">
                  <a-menu-item v-for="option in exportOptions" :key="`copy:${option.key}`">
                    复制{{ option.label }}Turn
                  </a-menu-item>
                  <a-menu-divider />
                  <a-menu-item v-for="option in exportOptions" :key="`download:${option.key}`">
                    下载{{ option.label }}Turn
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
            <button
              type="button"
              class="icon-button"
              aria-label="关闭详情"
              title="关闭详情"
              @click="selectedTargetKey = ''"
            >
              <X :size="15" aria-hidden="true" />
            </button>
          </div>
        </header>

        <div v-if="inspectorTab === 'overview'" class="inspector-overview">
          <dl>
            <div
              v-for="row in inspectorOverviewRows"
              :key="row.label"
              class="inspector-field"
              :class="{ 'inspector-field-content': row.content }"
            >
              <dt :title="row.description || ''">{{ row.label }}</dt>
              <dd>{{ row.value }}</dd>
            </div>
          </dl>
          <div
            v-if="selectedTarget.kind === 'turn' && selectedTarget.group.turnId"
            class="inspector-run-actions"
          >
            <button
              type="button"
              :disabled="isLangfuseTurnOpening(selectedTarget.group.turnId)"
              :aria-busy="isLangfuseTurnOpening(selectedTarget.group.turnId)"
              @click="openTurnInLangfuse(selectedTarget.group.turnId)"
            >
              <LoaderCircle
                v-if="isLangfuseTurnOpening(selectedTarget.group.turnId)"
                :size="13"
                class="spinning"
                aria-hidden="true"
              />
              <ExternalLink v-else :size="13" aria-hidden="true" />
              {{
                isLangfuseTurnOpening(selectedTarget.group.turnId)
                  ? '正在打开 Langfuse'
                  : '打开 Langfuse Trace'
              }}
            </button>
          </div>
          <p v-if="selectedTarget.kind === 'turn'" class="inspector-note">
            轮次总耗时包含等待与恢复间隔；各 Run 阶段和消息耗时分别来自持久状态与审计记录。
          </p>
        </div>
        <div v-else class="inspector-data">
          <JsonTreeViewer
            :data="selectedTargetData"
            :default-expanded-depth="1"
            :show-toolbar="false"
          />
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Check,
  ChevronRight,
  Clock,
  Copy,
  Ellipsis,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  Search,
  TriangleAlert,
  X
} from '@lucide/vue'
import { message } from 'ant-design-vue'
import { agentApi } from '@/apis/agent_api'
import JsonTreeViewer from '@/shared/ui/JsonTreeViewer.vue'
import { copyTextToClipboard } from '@/shared/lib/clipboard'
import {
  buildMessageDebugEntries,
  buildMessageDebugTurnData,
  buildMessageDebugTraceSpans,
  constrainMessageDebugInspectorHeight,
  constrainMessageDebugInspectorWidth,
  formatAuditDuration,
  formatMessageDebugContent,
  isMessageDebugEntryInTimeRange,
  isMessageDebugTimelineMarkSelected,
  mergeMessageDebugRunGroups,
  groupMessageDebugTurns,
  serializeMessageDebugTurn
} from '@/modules/session/model/messageDebug'
import {
  buildRunTimingPhases,
  buildRunTimingRows,
  buildSequentialRunTiming,
  buildTimelineRangeAtPoint,
  constrainTimelineRange,
  formatRunTimingDuration,
  getRunTimingWindow
} from '@/modules/session/model/runTiming'
import { formatDateTime } from '@/shared/lib/time'
import { resolveLangfuseTraceUrl } from '@/shared/lib/langfuse'

const props = defineProps({
  threadId: { type: String, default: null },
  active: { type: Boolean, default: false },
  activeRunId: { type: String, default: null },
  runActive: { type: Boolean, default: false }
})

const currentFilter = ref('all')
const searchQuery = ref('')
const isAllCopied = ref(false)
const isExportMenuOpen = ref(false)
const isExporting = ref(false)
const exportOptions = [
  { key: 'full', label: '完整' },
  { key: 'compact', label: '精简' },
  { key: 'minimal', label: '极简' }
]
const openingLangfuseTurnIds = ref(new Set())
const messageAudits = ref([])
const runTraces = ref([])
const turnTraces = ref([])
const auditsTruncated = ref(false)
const runsTruncated = ref(false)
const isAuditLoading = ref(false)
const auditLoadError = ref(false)
const pageVisible = ref(document.visibilityState === 'visible')
const timelineRangeStart = ref(0)
const timelineRangeEnd = ref(1000)
const isTraceOverviewVisible = ref(true)
const selectedTargetKey = ref('')
const inspectorTab = ref('overview')
const traceWorkbenchRef = ref(null)
const inspectorHeight = ref(null)
const inspectorWidth = ref(null)
const isWideInspectorLayout = ref(false)
let activeInspectorResizePointerId = null
let traceWorkbenchResizeObserver = null
let auditPollTimer = null
let auditRequestGeneration = 0
let auditRequestInFlight = false

const executionStatusLabels = {
  running: '运行中',
  waiting: '等待中',
  cancelling: '取消中',
  completed: '已完成',
  failed: '失败',
  interrupted: '已中断',
  yielded: '已让位',
  abandoned: '已放弃',
  cancelled: '已取消',
  cancel_requested: '取消中',
  pending: '等待中',
  sending: '发送中',
  queued: '排队中',
  rejected: '已拒绝',
  dispatched: '等待运行'
}
const roleNames = {
  human: '用户',
  ai: '模型',
  tool: '工具',
  error: '模型错误',
  system: '系统',
  other: '其他'
}

const loadMessageAudits = async (silent = false) => {
  if (!props.threadId || auditRequestInFlight) return
  const generation = ++auditRequestGeneration
  auditRequestInFlight = true
  if (!silent) isAuditLoading.value = true
  try {
    const result = await agentApi.getThreadMessageAudits(props.threadId)
    if (generation !== auditRequestGeneration) return
    messageAudits.value = Array.isArray(result?.audits) ? result.audits : []
    runTraces.value = Array.isArray(result?.runs) ? result.runs : []
    turnTraces.value = Array.isArray(result?.turns) ? result.turns : []
    auditsTruncated.value = result?.truncated === true
    runsTruncated.value = result?.runs_truncated === true
    auditLoadError.value = false
  } catch {
    if (generation === auditRequestGeneration) auditLoadError.value = true
  } finally {
    if (generation === auditRequestGeneration) {
      auditRequestInFlight = false
      if (!silent) isAuditLoading.value = false
    }
  }
}
const refreshMessageAudits = () => loadMessageAudits(false)
const stopAuditPolling = () => {
  if (auditPollTimer) window.clearInterval(auditPollTimer)
  auditPollTimer = null
}
const handlePageVisibilityChange = () => {
  pageVisible.value = document.visibilityState === 'visible'
}
document.addEventListener('visibilitychange', handlePageVisibilityChange)

watch(
  () => [props.threadId, props.active, props.activeRunId, props.runActive, pageVisible.value],
  ([threadId, active, activeRunId, runActive, isPageVisible], previous = []) => {
    stopAuditPolling()
    auditRequestGeneration += 1
    auditRequestInFlight = false
    isAuditLoading.value = false
    if (threadId !== previous[0]) {
      messageAudits.value = []
      runTraces.value = []
      turnTraces.value = []
      auditsTruncated.value = false
      runsTruncated.value = false
      auditLoadError.value = false
      selectedTargetKey.value = ''
      timelineRangeStart.value = 0
      timelineRangeEnd.value = 1000
      inspectorHeight.value = null
      inspectorWidth.value = null
    }
    if (!active || !threadId || !isPageVisible) return
    refreshMessageAudits()
    if (runActive && activeRunId) {
      auditPollTimer = window.setInterval(() => loadMessageAudits(true), 2000)
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  auditRequestGeneration += 1
  stopAuditPolling()
  traceWorkbenchResizeObserver?.disconnect()
  document.removeEventListener('visibilitychange', handlePageVisibilityChange)
})

onMounted(() => {
  const workbench = traceWorkbenchRef.value
  if (!workbench) return
  const updateInspectorLayout = () => {
    const bounds = workbench.getBoundingClientRect()
    isWideInspectorLayout.value = bounds.width >= 580
    if (bounds.width > 0 && inspectorWidth.value !== null) {
      inspectorWidth.value = constrainMessageDebugInspectorWidth(bounds.width, inspectorWidth.value)
    }
    if (bounds.height > 0 && inspectorHeight.value !== null) {
      inspectorHeight.value = constrainMessageDebugInspectorHeight(
        bounds.height,
        inspectorHeight.value
      )
    }
  }
  updateInspectorLayout()
  traceWorkbenchResizeObserver = new ResizeObserver(updateInspectorLayout)
  traceWorkbenchResizeObserver.observe(workbench)
})

const usageTokenCounts = (usage) => {
  const prompt = usage?.prompt_tokens ?? usage?.input_tokens ?? usage?.prompt ?? usage?.input ?? 0
  const completion =
    usage?.completion_tokens ?? usage?.output_tokens ?? usage?.completion ?? usage?.output ?? 0
  return {
    prompt,
    completion,
    total: usage?.total_tokens ?? usage?.total ?? prompt + completion
  }
}
const formatTokens = (usage) => {
  if (!usage) return ''
  const { total, prompt, completion } = usageTokenCounts(usage)
  if (!total && !prompt && !completion) return ''
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`
  return String(total)
}

const timelineItems = computed(() =>
  buildMessageDebugEntries(messageAudits.value).map(
    (item) => {
      const tokenCounts = usageTokenCounts(item.usage)
      const displaySummary =
        item.role === 'tool' && item.toolName ? `${item.toolName} · ${item.summary}` : item.summary
      return {
        ...item,
        displaySummary,
        tokenSummary: formatTokens(item.usage),
        tokenTooltip: item.usage
          ? `输入: ${tokenCounts.prompt}, 输出: ${tokenCounts.completion}, 总计: ${tokenCounts.total}`
          : ''
      }
    }
  )
)
const runTraceById = computed(() => new Map(runTraces.value.map((run) => [run.run_id, run])))
const runGroups = computed(() =>
  mergeMessageDebugRunGroups(timelineItems.value, runTraces.value).map((group, index) => {
    const runTrace = runTraceById.value.get(group.runId) || null
    const timing = runTrace?.timing
    return {
      ...group,
      index,
      turnId: runTrace?.turn_id || group.items.find((item) => item.turnId)?.turnId || null,
      inputId: group.inputId || runTrace?.input_id || null,
      runTrace,
      timing,
      timingRows: buildRunTimingRows(timing),
      traceWindow: getRunTimingWindow(timing)
    }
  })
)
const baseGroups = computed(() => {
  const turns = new Map(turnTraces.value.map((turn) => [turn.turn_id, turn]))
  return groupMessageDebugTurns(runGroups.value).map((group) => ({
    ...group,
    turnTrace: turns.get(group.turnId) || null
  }))
})
const turnKeyByRunGroup = computed(() => new Map(
  baseGroups.value.flatMap((group) => group.runs.map((run) => [run.key, group.key]))
))
const combinedTrace = computed(() => {
  const timeline = buildSequentialRunTiming(runGroups.value.map((group) => group.timing))
  if (!timeline) return null
  const segmentByGroupKey = new Map()
  const phases = []
  const spans = []

  timeline.segments.forEach((segment) => {
    const group = runGroups.value[segment.sourceIndex]
    segmentByGroupKey.set(group.key, segment)
    buildRunTimingPhases(group.timing).forEach((phase) => {
      const startOffsetMs = segment.startOffsetMs + phase.startOffsetMs
      const endOffsetMs = segment.startOffsetMs + phase.endOffsetMs
      phases.push({
        ...phase,
        groupKey: turnKeyByRunGroup.value.get(group.key),
        type: phase.key,
        key: `${group.key}:${phase.key}`,
        startOffsetMs,
        endOffsetMs,
        leftPercent: (startOffsetMs / timeline.durationMs) * 100,
        widthPercent: Math.max(((endOffsetMs - startOffsetMs) / timeline.durationMs) * 100, 0.35)
      })
    })
    const itemsById = new Map(group.items.map((item) => [item.id, item]))
    buildMessageDebugTraceSpans(group.items, segment.window).forEach((span) => {
      const startOffsetMs = segment.startOffsetMs + span.startOffsetMs
      const endOffsetMs = segment.startOffsetMs + span.endOffsetMs
      spans.push({
        ...span,
        groupKey: turnKeyByRunGroup.value.get(group.key),
        key: `${group.key}:${span.key}`,
        item: itemsById.get(span.key),
        startOffsetMs,
        endOffsetMs,
        leftPercent: (startOffsetMs / timeline.durationMs) * 100,
        widthPercent: Math.max(((endOffsetMs - startOffsetMs) / timeline.durationMs) * 100, 0.35)
      })
    })
  })
  return { ...timeline, segmentByGroupKey, phases, spans }
})
const timelineRangeStartPercent = computed(() => timelineRangeStart.value / 10)
const timelineRangeEndPercent = computed(() => timelineRangeEnd.value / 10)
const isTimelineRangeActive = computed(
  () => timelineRangeStart.value > 0 || timelineRangeEnd.value < 1000
)
const traceDataTruncated = computed(() => auditsTruncated.value || runsTruncated.value)
const formatTimelineOffset = (ratio) => {
  const duration = combinedTrace.value?.durationMs
  if (!Number.isFinite(duration)) return ''
  if (ratio === 0) return '0'
  return formatRunTimingDuration(duration * ratio)
}
const timelineAxisTicks = computed(() =>
  [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({ ratio, label: formatTimelineOffset(ratio) }))
)
const timelineRangeLabel = computed(() => {
  const start = formatTimelineOffset(timelineRangeStart.value / 1000)
  const end = formatTimelineOffset(timelineRangeEnd.value / 1000)
  return isTimelineRangeActive.value ? `${start} 至 ${end}` : `全部 ${end}`
})
const timelineRangeAriaLabel = computed(
  () => `当前时间范围：${timelineRangeLabel.value}；点击轨道选择两秒窗口`
)
const applyTimelineRange = (range) => {
  timelineRangeStart.value = range.start
  timelineRangeEnd.value = range.end
}
const resetTimelineRange = () => {
  applyTimelineRange({ start: 0, end: 1000 })
}
const toggleTraceOverview = () => {
  isTraceOverviewVisible.value = !isTraceOverviewVisible.value
  if (!isTraceOverviewVisible.value) resetTimelineRange()
}
const updateTimelineRangeStart = (event) => {
  const range = constrainTimelineRange(
    combinedTrace.value?.durationMs,
    event.target.value,
    timelineRangeEnd.value,
    'start'
  )
  event.target.value = String(range.start)
  applyTimelineRange(range)
}
const updateTimelineRangeEnd = (event) => {
  const range = constrainTimelineRange(
    combinedTrace.value?.durationMs,
    timelineRangeStart.value,
    event.target.value,
    'end'
  )
  event.target.value = String(range.end)
  applyTimelineRange(range)
}
const selectTimelineWindowAtPointer = (event) => {
  if (event.target?.classList?.contains('range-input')) return
  const bounds = event.currentTarget.getBoundingClientRect()
  if (bounds.width <= 0) return
  const pointRatio = (event.clientX - bounds.left) / bounds.width
  applyTimelineRange(buildTimelineRangeAtPoint(combinedTrace.value?.durationMs, pointRatio))
}

watch(
  () => combinedTrace.value?.durationMs,
  (durationMs) => {
    if (!isTimelineRangeActive.value) return
    const range = constrainTimelineRange(
      durationMs,
      timelineRangeStart.value,
      timelineRangeEnd.value
    )
    applyTimelineRange(range)
  }
)

const traceWorkbenchStyle = computed(() => ({
  ...(inspectorHeight.value === null ? {} : { '--inspector-height': `${inspectorHeight.value}px` }),
  ...(inspectorWidth.value === null ? {} : { '--inspector-width': `${inspectorWidth.value}px` })
}))
const inspectorSizePercent = computed(() => {
  const bounds = traceWorkbenchRef.value?.getBoundingClientRect()
  const containerSize = isWideInspectorLayout.value ? bounds?.width : bounds?.height
  if (!Number.isFinite(containerSize) || containerSize <= 0) return 42
  const inspectorSize = isWideInspectorLayout.value
    ? (inspectorWidth.value ?? containerSize * 0.42)
    : (inspectorHeight.value ?? containerSize * 0.42)
  return Math.round((inspectorSize / containerSize) * 100)
})
const setInspectorHeight = (requestedHeight) => {
  const containerHeight = traceWorkbenchRef.value?.getBoundingClientRect().height
  const height = constrainMessageDebugInspectorHeight(containerHeight, requestedHeight)
  if (height !== null) inspectorHeight.value = height
}
const setInspectorWidth = (requestedWidth) => {
  const containerWidth = traceWorkbenchRef.value?.getBoundingClientRect().width
  const width = constrainMessageDebugInspectorWidth(containerWidth, requestedWidth)
  if (width !== null) inspectorWidth.value = width
}
const resizeInspectorAtPointer = (event) => {
  const bounds = traceWorkbenchRef.value?.getBoundingClientRect()
  if (!bounds) return
  if (isWideInspectorLayout.value) setInspectorWidth(bounds.right - event.clientX)
  else setInspectorHeight(bounds.bottom - event.clientY)
}
const startInspectorResize = (event) => {
  event.preventDefault()
  activeInspectorResizePointerId = event.pointerId
  event.currentTarget.setPointerCapture(event.pointerId)
  resizeInspectorAtPointer(event)
}
const moveInspectorResize = (event) => {
  if (event.pointerId !== activeInspectorResizePointerId) return
  resizeInspectorAtPointer(event)
}
const stopInspectorResize = (event) => {
  if (event.pointerId !== activeInspectorResizePointerId) return
  activeInspectorResizePointerId = null
  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId)
  }
}
const resizeInspectorWithKeyboard = (event) => {
  const resizeKeys = isWideInspectorLayout.value
    ? ['ArrowLeft', 'ArrowRight']
    : ['ArrowUp', 'ArrowDown']
  if (!resizeKeys.includes(event.key)) return
  event.preventDefault()
  const bounds = traceWorkbenchRef.value?.getBoundingClientRect()
  if (!bounds) return
  if (isWideInspectorLayout.value) {
    const currentWidth = inspectorWidth.value ?? bounds.width * 0.42
    setInspectorWidth(currentWidth + (event.key === 'ArrowLeft' ? 24 : -24))
    return
  }
  const currentHeight = inspectorHeight.value ?? bounds.height * 0.42
  setInspectorHeight(currentHeight + (event.key === 'ArrowUp' ? 24 : -24))
}
function isFailureStatus(status) {
  return ['failed', 'interrupted', 'abandoned'].includes(status)
}

const filterTabs = computed(() => {
  const all = timelineItems.value
  return [
    { key: 'all', label: '全部', count: all.length },
    { key: 'human', label: '用户', count: all.filter((item) => item.role === 'human').length },
    { key: 'ai', label: 'AI', count: all.filter((item) => item.role === 'ai').length },
    { key: 'tool', label: '工具', count: all.filter((item) => item.role === 'tool').length },
    {
      key: 'error',
      label: '错误',
      count: all.filter((item) => item.role === 'error' || isFailureStatus(item.executionStatus))
        .length
    },
    { key: 'system', label: '系统', count: all.filter((item) => item.role === 'system').length }
  ]
})
const itemMatchesFilters = (item) => {
  if (currentFilter.value === 'error') {
    if (item.role !== 'error' && !isFailureStatus(item.executionStatus)) return false
  } else if (currentFilter.value !== 'all' && item.role !== currentFilter.value) {
    return false
  }
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return true
  return (
    item.roleLabel.toLowerCase().includes(query) ||
    item.displaySummary.toLowerCase().includes(query) ||
    String(item.model || '')
      .toLowerCase()
      .includes(query) ||
    String(JSON.stringify(item.raw) || '')
      .toLowerCase()
      .includes(query)
  )
}
const filteredTimelineGroups = computed(() =>
  baseGroups.value.flatMap((group) => {
    const visibleItems = group.runs.flatMap((run) => {
      const segment = isTimelineRangeActive.value
        ? combinedTrace.value?.segmentByGroupKey.get(run.key)
        : null
      return run.items.filter((item) => {
        if (!itemMatchesFilters(item)) return false
        if (!isTimelineRangeActive.value) return true
        return isMessageDebugEntryInTimeRange(
          item,
          segment?.window,
          segment?.startOffsetMs,
          combinedTrace.value?.durationMs,
          timelineRangeStart.value / 1000,
          timelineRangeEnd.value / 1000
        )
      })
    })
    if (visibleItems.length) return [{ ...group, visibleItems }]
    const emptyRuns = group.runs.filter((run) => run.items.length === 0)
    if (!emptyRuns.length || currentFilter.value !== 'all') return []
    const query = searchQuery.value.trim().toLowerCase()
    if (
      query &&
      !JSON.stringify({ turn: group.turnTrace, runs: emptyRuns.map((run) => run.runTrace) })
        .toLowerCase()
        .includes(query)
    )
      return []
    if (isTimelineRangeActive.value && combinedTrace.value) {
      const rangeStartMs = combinedTrace.value.durationMs * (timelineRangeStart.value / 1000)
      const rangeEndMs = combinedTrace.value.durationMs * (timelineRangeEnd.value / 1000)
      const segments = emptyRuns.map((run) => combinedTrace.value.segmentByGroupKey.get(run.key)).filter(Boolean)
      if (segments.length && !segments.some((segment) => segment.endOffsetMs >= rangeStartMs && segment.startOffsetMs <= rangeEndMs)) return []
    }
    return [{ ...group, visibleItems: [] }]
  })
)
const visibleItemCount = computed(() =>
  filteredTimelineGroups.value.reduce((count, group) => count + group.visibleItems.length + 1, 0)
)
const emptyTimelineText = computed(() => {
  if (isAuditLoading.value) return '正在读取审计…'
  if (isTimelineRangeActive.value) return '所选时间范围内没有匹配记录'
  if (searchQuery.value) return '没有匹配的记录'
  return '当前会话暂无消息数据'
})

const turnStatus = (group) => {
  if (group.turnTrace?.status) return group.turnTrace.status
  if (!group.turnId && group.inputId) {
    return group.items.find((item) => item.role === 'human')?.raw?.delivery_status || ''
  }
  return ''
}
const turnTitle = (group) => {
  if (group.turnId) return `Turn ${group.index + 1}`
  if (group.inputId) return turnStatus(group) ? formatExecutionStatus(turnStatus(group)) : '输入'
  return '未关联轮次'
}
const formatExecutionStatus = (status) => executionStatusLabels[status] || status || '状态未知'
const itemRowAriaLabel = (item) => {
  const status = item.executionStatus ? `，${formatExecutionStatus(item.executionStatus)}` : ''
  return `${roleNames[item.role] || '记录'}${status}：${item.displaySummary}`
}
const turnRowAriaLabel = (group) => {
  const status = turnStatus(group)
  return `${turnTitle(group)}${group.turnId && status ? `，${formatExecutionStatus(status)}` : ''}：${turnSummary(group)}`
}
const turnSummary = (group) => {
  const userMessage = group.items.find((item) => item.role === 'human')
  if (userMessage?.summary) return userMessage.summary
  return `${group.items.length} 条记录`
}
const formatRecordTime = (item) => {
  const value = item.finishedAt || item.startedAt || item.createdAt
  return value ? formatDateTime(value, 'HH:mm:ss') : ''
}
const formatAuditTimeTooltip = (item) =>
  [
    item.createdAt ? `记录：${formatDateTime(item.createdAt, 'YYYY-MM-DD HH:mm:ss')}` : '',
    item.startedAt ? `开始：${formatDateTime(item.startedAt, 'YYYY-MM-DD HH:mm:ss')}` : '',
    item.finishedAt ? `结束：${formatDateTime(item.finishedAt, 'YYYY-MM-DD HH:mm:ss')}` : '',
    item.operationId ? `Operation：${item.operationId}` : ''
  ]
    .filter(Boolean)
    .join('\n')
const formatTurnTime = (group) => {
  const sentAt = group.items.find((item) => item.role === 'human')?.createdAt
  const value = group.turnTrace?.created_at || sentAt
  return value ? formatDateTime(value, 'HH:mm:ss') : ''
}
const formatTurnDuration = (group) => {
  const turn = group.turnTrace
  if (!turn?.created_at || !turn.finished_at) return ''
  return formatRunTimingDuration(Date.parse(turn.finished_at) - Date.parse(turn.created_at))
}
const traceSpanTitle = (span) => {
  const item = span.item
  if (!item) return ''
  const duration = formatAuditDuration(item.durationMs)
  return [roleNames[item.role] || '记录', item.displaySummary, duration].filter(Boolean).join(' · ')
}

const turnTargetKey = (group) => `turn:${group.key}`
const itemTargetKey = (group, item) => `item:${group.key}:${item.id}`
const isTimelineMarkSelected = (mark) =>
  isMessageDebugTimelineMarkSelected(selectedTargetKey.value, mark.groupKey, mark.item?.id)
const selectTurn = (group) => {
  selectedTargetKey.value = turnTargetKey(group)
  inspectorTab.value = 'overview'
}
const selectItem = (group, item) => {
  selectedTargetKey.value = itemTargetKey(group, item)
  inspectorTab.value = 'overview'
}
const selectedTarget = computed(() => {
  for (const group of baseGroups.value) {
    if (selectedTargetKey.value === turnTargetKey(group)) return { kind: 'turn', group }
    const item = group.items.find(
      (candidate) => selectedTargetKey.value === itemTargetKey(group, candidate)
    )
    if (item) return { kind: 'item', group, item }
  }
  return null
})
const selectedTargetData = computed(() => {
  if (!selectedTarget.value) return null
  if (selectedTarget.value.kind === 'item') return selectedTarget.value.item.raw
  return buildMessageDebugTurnData(selectedTarget.value.group)
})
const inspectorOverviewRows = computed(() => {
  if (!selectedTarget.value) return []
  if (selectedTarget.value.kind === 'turn') {
    const group = selectedTarget.value.group
    return [
      { label: '状态', value: formatExecutionStatus(turnStatus(group)) },
      { label: '记录', value: `${group.items.length} 条` },
      { label: '创建时间', value: formatTurnTime(group) },
      { label: '轮次总耗时', value: formatTurnDuration(group) || '暂无数据' },
      ...group.runs.flatMap((run, index) => [
        { label: `Run ${index + 1} 状态`, value: formatExecutionStatus(run.runTrace?.status) },
        ...run.timingRows.map((row) => ({
          label: `Run ${index + 1} · ${row.label}`,
          value: row.formattedValue,
          description: row.description
        }))
      ])
    ]
  }
  const item = selectedTarget.value.item
  const content = formatMessageDebugContent(item.raw?.content)
  return [
    { label: '类型', value: roleNames[item.role] || '其他' },
    {
      label: '状态',
      value: item.executionStatus ? formatExecutionStatus(item.executionStatus) : ''
    },
    { label: 'Operation', value: item.operationId || '' },
    { label: '序号', value: item.sequence === null ? '' : String(item.sequence) },
    {
      label: '记录时间',
      value: item.createdAt ? formatDateTime(item.createdAt, 'YYYY-MM-DD HH:mm:ss') : ''
    },
    {
      label: '开始时间',
      value: item.startedAt ? formatDateTime(item.startedAt, 'YYYY-MM-DD HH:mm:ss') : ''
    },
    {
      label: '结束时间',
      value: item.finishedAt ? formatDateTime(item.finishedAt, 'YYYY-MM-DD HH:mm:ss') : ''
    },
    { label: '单调耗时', value: formatAuditDuration(item.durationMs) },
    { label: '模型', value: item.model || '' },
    { label: 'Tokens', value: item.tokenTooltip || '' },
    { label: '错误类型', value: item.raw?.error_type || '' },
    { label: '错误信息', value: item.raw?.error_message || '', content: true },
    { label: 'Content', value: content, content: true }
  ].filter((row) => row.value)
})

const isLangfuseTurnOpening = (turnId) => openingLangfuseTurnIds.value.has(turnId)
const setLangfuseTurnOpening = (turnId, opening) => {
  const next = new Set(openingLangfuseTurnIds.value)
  if (opening) next.add(turnId)
  else next.delete(turnId)
  openingLangfuseTurnIds.value = next
}
const openTurnInLangfuse = async (turnId) => {
  if (!turnId || isLangfuseTurnOpening(turnId)) return

  const targetWindow = window.open('about:blank', '_blank')
  if (!targetWindow) {
    message.warning('浏览器阻止了新标签页，请允许弹窗后重试')
    return
  }
  targetWindow.opener = null
  const threadId = props.threadId
  setLangfuseTurnOpening(turnId, true)

  try {
    const result = await agentApi.getTurnLangfuse(threadId, turnId)
    if (threadId !== props.threadId) {
      targetWindow.close()
      return
    }
    const traceUrl = resolveLangfuseTraceUrl(result)
    if (!traceUrl) {
      targetWindow.close()
      if (!result?.langfuse_trace_id) {
        message.warning('本轮暂无可用的 Langfuse Trace')
      } else {
        message.error('Langfuse 当前不可用，请检查配置或稍后重试')
      }
      return
    }
    targetWindow.location.replace(traceUrl)
  } catch {
    targetWindow.close()
    message.error('获取 Langfuse 跳转地址失败，请稍后重试')
  } finally {
    setLangfuseTurnOpening(turnId, false)
  }
}

const exportSelectedTurn = async ({ key }) => {
  if (!selectedTarget.value || isExporting.value) return
  const group = selectedTarget.value.group
  const [action, detail] = key.split(':')
  isExportMenuOpen.value = false
  isExporting.value = true
  try {
    const json = await serializeMessageDebugTurn(group, detail)
    if (action === 'copy') {
      await copyTextToClipboard(json)
      message.success('已复制 Turn')
    } else {
      const url = URL.createObjectURL(new Blob([json], { type: 'application/json;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `turn-${group.index + 1}-${detail}.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      message.success('已下载 Turn')
    }
  } catch {
    message.error(action === 'copy' ? '复制失败' : '下载失败')
  } finally {
    isExporting.value = false
  }
}
const copyAllTimelineJson = async () => {
  try {
    const allData = timelineItems.value.map((item) => ({
      role: item.roleLabel,
      model: item.model,
      raw: item.raw
    }))
    await copyTextToClipboard(JSON.stringify(allData, null, 2))
    isAllCopied.value = true
    message.success('已复制全部消息数据')
    window.setTimeout(() => {
      isAllCopied.value = false
    }, 1500)
  } catch {
    message.error('复制失败')
  }
}
</script>

<style scoped lang="less">
.message-debug-panel {
  display: flex;
  height: 100%;
  overflow: hidden;
  flex-direction: column;
  container-type: inline-size;
  background: var(--gray-0);
  color: var(--gray-900);
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.debug-toolbar {
  display: flex;
  min-height: 40px;
  padding: 0 10px;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-0);
}

.toolbar-actions,
.range-summary,
.inspector-actions {
  display: flex;
  align-items: center;
}

.truncated-note {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--color-warning-700);
  font-size: 11px;
  font-weight: 400;
}

.toolbar-actions {
  flex: 0 0 auto;
  gap: 2px;
}

.icon-button {
  display: inline-flex;
  width: 28px;
  height: 26px;
  padding: 0;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;

  &:hover:not(:disabled) {
    border-color: var(--gray-200);
    background: var(--gray-50);
    color: var(--gray-1000);
  }

  &:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: -1px;
  }

  &:disabled {
    color: var(--gray-300);
    cursor: default;
  }

  &.compact {
    width: 22px;
    height: 22px;
  }

  &.active {
    border-color: var(--gray-200);
    background: var(--gray-100);
    color: var(--gray-900);
  }
}

.success-icon {
  color: var(--color-success-700);
}

.filter-select-field {
  min-width: 100px;
  flex: 0 0 auto;
}

.search-field {
  display: flex;
  width: clamp(130px, 24cqw, 220px);
  min-width: 130px;
  height: 25px;
  padding: 0 7px;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--gray-300);
  border-radius: 4px;
  color: var(--gray-400);
  margin-left: auto;

  &:focus-within {
    border-color: var(--main-500);
    box-shadow: 0 0 0 1px var(--main-100);
  }

  input {
    width: 100%;
    min-width: 0;
    border: 0;
    outline: 0;
    background: transparent;
    color: var(--gray-900);
    font-size: 11.5px;
  }

  input::placeholder {
    color: var(--gray-400);
  }
}

.audit-error {
  display: flex;
  min-height: 30px;
  padding: 0 10px;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid var(--color-error-100);
  background: var(--color-error-50);
  color: var(--color-error-700);
  font-size: 11.5px;

  span {
    min-width: 0;
    flex: 1;
  }

  button {
    border: 0;
    background: transparent;
    color: inherit;
    font-weight: 600;
    cursor: pointer;
  }
}

.trace-overview {
  padding: 7px 14px 8px;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-25);
}

.trace-overview-header {
  display: flex;
  min-height: 23px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--gray-700);
  font-size: 11px;
}

.trace-overview-header > span:first-child {
  color: var(--gray-800);
  font-weight: 600;
}

.range-summary {
  gap: 8px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-variant-numeric: tabular-nums;
}

.reset-range-button {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-color);
  font-family: inherit;
  cursor: pointer;
}

.trace-axis {
  position: relative;
  height: 18px;
  margin: 0 5px;
  color: var(--gray-500);
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}

.axis-tick {
  position: absolute;
  bottom: 2px;
  transform: translateX(-50%);
  white-space: nowrap;

  &:first-child {
    transform: none;
  }

  &:last-child {
    transform: translateX(-100%);
  }
}

.trace-track {
  position: relative;
  height: 34px;
  margin: 0 5px;
  overflow: visible;
  border: 1px solid var(--gray-300);
  background: var(--gray-0);
  cursor: crosshair;
}

.track-grid {
  position: absolute;
  inset: 0;
  background-image: repeating-linear-gradient(
    to right,
    transparent 0,
    transparent calc(25% - 1px),
    var(--gray-150) calc(25% - 1px),
    var(--gray-150) 25%
  );
}

.phase-lane,
.operation-lane {
  position: absolute;
  right: 0;
  left: 0;
}

.phase-lane {
  top: 7px;
  height: 6px;
}

.operation-lane {
  top: 19px;
  height: 8px;
}

.phase-segment,
.operation-span {
  position: absolute;
  min-width: 2px;
  transition:
    outline-color 120ms ease,
    box-shadow 120ms ease,
    filter 120ms ease;
}

.timeline-mark-selected {
  z-index: 6;
  outline: 1px solid var(--gray-900);
  outline-offset: 1px;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--gray-0) 82%, transparent);
  filter: saturate(1.25) contrast(1.08);
}

.phase-segment {
  height: 6px;
  background: var(--main-300);
}

.phase-dispatch {
  background: var(--gray-400);
}

.phase-preparation {
  background: var(--second-400);
}

.phase-first-output {
  background: var(--main-500);
}

.phase-completion {
  background: var(--main-200);
}

.operation-span {
  height: 8px;
  border-left: 2px solid var(--gray-500);
  background: color-mix(in srgb, var(--gray-400) 45%, transparent);
}

.span-ai {
  border-left-color: var(--main-700);
  background: color-mix(in srgb, var(--main-400) 45%, transparent);
}

.span-tool {
  border-left-color: var(--second-700);
  background: color-mix(in srgb, var(--second-300) 55%, transparent);
}

.span-human {
  border-left-color: var(--gray-800);
}

.span-error {
  border-left-color: var(--color-error-700);
  background: color-mix(in srgb, var(--color-error-500) 38%, transparent);
}

.operation-span.span-timing-fallback {
  height: 6px;
  margin-top: 1px;
  border-top: 1px dashed var(--gray-400);
  border-bottom: 1px dashed var(--gray-400);
  background: transparent;
  opacity: 0.55;
}

.operation-span.span-timing-fallback.timeline-mark-selected {
  opacity: 1;
}

.range-mask {
  position: absolute;
  z-index: 2;
  top: 0;
  bottom: 0;
  background: color-mix(in srgb, var(--gray-200) 78%, transparent);
  pointer-events: none;
}

.range-mask-start {
  left: 0;
}

.range-mask-end {
  right: 0;
}

.selected-window {
  position: absolute;
  z-index: 3;
  top: -1px;
  bottom: -1px;
  border-top: 1px solid var(--main-500);
  border-bottom: 1px solid var(--main-500);
  background: color-mix(in srgb, var(--main-100) 18%, transparent);
  opacity: 0;
  pointer-events: none;
  transition: opacity 140ms ease-out;
}

.range-input {
  position: absolute;
  z-index: 4;
  inset: 0;
  width: 100%;
  height: 34px;
  margin: 0;
  appearance: none;
  background: transparent;
  pointer-events: none;

  &:focus-visible {
    outline: none;
  }

  &::-webkit-slider-runnable-track {
    height: 34px;
    background: transparent;
  }

  &::-webkit-slider-thumb {
    width: 10px;
    height: 36px;
    margin-top: -1px;
    appearance: none;
    border: 2px solid var(--main-600);
    border-radius: 2px;
    background: var(--gray-0);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--gray-0) 70%, transparent);
    cursor: ew-resize;
    opacity: 0;
    pointer-events: auto;
    transition: opacity 140ms ease-out;
  }

  &::-moz-range-track {
    height: 34px;
    background: transparent;
  }

  &::-moz-range-thumb {
    width: 8px;
    height: 32px;
    border: 2px solid var(--main-600);
    border-radius: 2px;
    background: var(--gray-0);
    cursor: ew-resize;
    opacity: 0;
    pointer-events: auto;
    transition: opacity 140ms ease-out;
  }

  &:focus-visible::-webkit-slider-thumb {
    box-shadow: 0 0 0 3px var(--main-100);
  }

  &:focus-visible::-moz-range-thumb {
    box-shadow: 0 0 0 3px var(--main-100);
  }
}

.trace-track:hover,
.trace-track:focus-within,
.trace-track.range-active {
  .selected-window,
  .range-input::-webkit-slider-thumb {
    opacity: 1;
  }

  .range-input::-moz-range-thumb {
    opacity: 1;
  }
}

.range-input-start {
  z-index: 5;
}

.trace-caption,
.trace-unavailable {
  display: flex;
  align-items: center;
  color: var(--gray-500);
  font-size: 10px;
}

.trace-caption {
  min-height: 19px;
  justify-content: space-between;
}

.trace-unavailable {
  min-height: 33px;
  padding: 0 12px;
  flex: 0 0 auto;
  gap: 6px;
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-25);
}

.trace-workbench {
  display: grid;
  min-height: 0;
  flex: 1;
  grid-template-rows: minmax(0, 1fr);

  &.has-inspector {
    grid-template-rows: minmax(120px, 1fr) 4px minmax(0, var(--inspector-height, 42%));
  }
}

.records-pane,
.record-inspector {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.records-pane {
  background: var(--gray-0);
}

.record-table-header,
.record-row {
  display: grid;
  grid-template-columns: 64px minmax(160px, 1fr) 76px 66px 70px 20px;
  align-items: center;
}

.record-table-header {
  position: sticky;
  z-index: 3;
  top: 0;
  min-height: 25px;
  padding: 0 10px;
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-25);
  color: var(--gray-500);
  font-size: 10px;
}

.record-list {
  min-width: 410px;
}

.record-row {
  width: 100%;
  min-height: 36px;
  padding: 0 10px;
  border: 0;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  color: var(--gray-900);
  text-align: left;
  cursor: pointer;

  &:hover {
    background: var(--gray-25);
  }

  &:focus-visible {
    position: relative;
    z-index: 2;
    outline: 2px solid var(--main-400);
    outline-offset: -2px;
  }

  &.selected {
    background: var(--main-40);
    box-shadow: inset 2px 0 var(--main-color);
  }
}

.run-row {
  min-height: 38px;
  background: var(--gray-25);
}

.record-kind {
  display: inline-flex;
  justify-self: start;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-700);
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
}

.kind-human {
  background: var(--color-success-50);
  color: var(--color-success-900);
}

.kind-ai {
  background: var(--main-50);
  color: var(--main-700);
}

.kind-tool {
  background: var(--color-warning-50);
  color: var(--color-warning-900);
}

.kind-error {
  background: var(--color-error-50);
  color: var(--color-error-700);
}

.record-summary {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--gray-800);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-summary {
  display: flex;
  align-items: baseline;
  gap: 9px;

  strong {
    flex: 0 0 auto;
    color: var(--gray-1000);
    font-size: 12px;
  }

  span {
    min-width: 0;
    overflow: hidden;
    color: var(--gray-600);
    font-weight: 400;
    text-overflow: ellipsis;
  }
}

.record-cell {
  overflow: hidden;
  color: var(--gray-600);
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-chevron {
  justify-self: end;
  color: var(--gray-400);
}

.empty-records {
  display: flex;
  height: 150px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  color: var(--gray-400);
  font-size: 11.5px;
}

.record-inspector {
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
}

.inspector-resizer {
  position: relative;
  z-index: 4;
  display: flex;
  min-height: 4px;
  padding: 0;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  cursor: row-resize;
  touch-action: none;

  &::before {
    position: absolute;
    inset: -4px 0;
    content: '';
  }

  span {
    width: 24px;
    height: 1px;
    background: var(--gray-300);
  }

  &:hover,
  &:focus-visible {
    outline: none;
  }

  &:hover span,
  &:focus-visible span {
    background: var(--main-600);
  }
}

.inspector-toolbar {
  display: flex;
  min-height: 36px;
  padding: 0 6px 0 10px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-top: 1px solid var(--gray-200);
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-25);
}

.inspector-actions {
  flex: 0 0 auto;
  gap: 2px;
}

.inspector-tabs {
  display: flex;
  min-height: 35px;
  padding: 0;
  flex: 0 0 auto;
  align-self: stretch;
  gap: 14px;

  button {
    padding: 0 2px;
    border: 0;
    border-bottom: 2px solid transparent;
    background: transparent;
    color: var(--gray-600);
    font-size: 11.5px;
    cursor: pointer;
  }

  button.active {
    border-bottom-color: var(--main-color);
    color: var(--gray-1000);
    font-weight: 600;
  }

  button:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: -2px;
  }
}

.inspector-overview,
.inspector-data {
  min-height: 0;
  overflow: auto;
  flex: 1;
}

.inspector-overview dl {
  margin: 0;
}

.inspector-field {
  display: grid;
  min-height: 31px;
  padding: 6px 12px;
  grid-template-columns: 110px minmax(0, 1fr);
  align-items: baseline;
  border-bottom: 1px solid var(--gray-150);
  font-size: 11px;

  dt {
    color: var(--gray-500);
  }

  dd {
    margin: 0;
    overflow-wrap: anywhere;
    color: var(--gray-800);
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  }
}

.inspector-field-content {
  grid-template-columns: minmax(0, 1fr);
  align-items: start;

  dt,
  dd {
    grid-column: 1 / -1;
  }

  dd {
    line-height: 1.55;
    white-space: pre-wrap;
  }
}

.inspector-note {
  margin: 10px 12px;
  color: var(--gray-500);
  font-size: 10.5px;
  line-height: 1.55;
}

.inspector-run-actions {
  padding: 10px 12px 0;

  button {
    display: inline-flex;
    min-height: 26px;
    padding: 0 8px;
    align-items: center;
    gap: 5px;
    border: 1px solid var(--gray-300);
    border-radius: 4px;
    background: var(--gray-0);
    color: var(--gray-700);
    font: inherit;
    font-size: 11px;
    cursor: pointer;
  }

  button:hover:not(:disabled) {
    border-color: var(--gray-400);
    background: var(--gray-50);
    color: var(--gray-1000);
  }

  button:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: 2px;
  }

  button:disabled {
    color: var(--gray-400);
    cursor: wait;
  }
}

.inspector-data {
  padding: 7px 9px 12px;
  font-size: 11px;
}

.spinning {
  animation: debug-spin 0.8s linear infinite;
}

@keyframes debug-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation-duration: 1.8s;
  }

  .selected-window,
  .range-input::-webkit-slider-thumb,
  .range-input::-moz-range-thumb {
    transition-duration: 0.01ms;
  }
}

@container (min-width: 580px) {
  .trace-workbench.has-inspector {
    grid-template-columns: minmax(300px, 1fr) 4px minmax(260px, var(--inspector-width, 42%));
    grid-template-rows: minmax(0, 1fr);
  }

  .inspector-resizer {
    min-width: 4px;
    min-height: 0;
    cursor: col-resize;

    &::before {
      inset: 0 -4px;
    }

    span {
      width: 1px;
      height: 24px;
    }
  }

  .record-inspector {
    border-left: 1px solid var(--gray-200);
  }
}

@container (max-width: 620px) {
  .record-table-header,
  .record-row {
    grid-template-columns: 64px minmax(150px, 1fr) 64px 20px;
  }

  .column-time,
  .column-tokens {
    display: none;
  }

  .record-list {
    min-width: 300px;
  }

  .trace-caption span:last-child {
    display: none;
  }
}

@container (max-width: 470px) {
  .debug-toolbar {
    padding: 0 8px;
    gap: 5px;
  }

  .search-field {
    width: auto;
    min-width: 0;
    max-width: 112px;
    flex: 1 1 72px;
  }

  .truncated-label {
    display: none;
  }

  .trace-overview {
    padding-right: 10px;
    padding-left: 10px;
  }

  .trace-overview-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 1px;
  }

  .range-summary {
    align-self: flex-end;
  }

  .inspector-field {
    grid-template-columns: 92px minmax(0, 1fr);
  }
}
</style>
