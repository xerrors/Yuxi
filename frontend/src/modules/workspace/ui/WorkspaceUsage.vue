<template>
  <div class="workspace-usage">
    <div class="usage-heading">
      <span class="usage-title">空间用量</span>
      <a-tooltip placement="topLeft" :trigger="['hover', 'focus']" :open="infoOpen" @open-change="setInfoOpen">
        <template #title>
          <div v-for="detail in details" :key="detail">{{ detail }}</div>
        </template>
        <button
          type="button"
          class="usage-info"
          aria-label="用量说明"
          :aria-describedby="infoId"
          :aria-expanded="infoOpen"
          @click="toggleInfo"
          @blur="closeInfo"
          @keydown.esc.stop="closeInfo"
        >
          <Info :size="14" aria-hidden="true" />
        </button>
      </a-tooltip>
      <span :id="infoId" hidden>{{ details.join('。') }}</span>
      <span class="usage-percent" :class="{ 'is-full': usage?.over_quota }" aria-hidden="true">
        {{ percentLabel }}
      </span>
    </div>
    <template v-if="percent !== null">
      <div
        class="usage-track"
        :class="{ 'is-full': usage.over_quota }"
        role="meter"
        aria-label="个人空间用量"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="meterValue"
        :aria-valuetext="amount"
      >
        <div v-if="meterValue > 0" class="usage-fill" :style="{ width: `${meterValue}%` }" />
      </div>
      <div class="usage-amount" role="status">{{ amount }}</div>
    </template>
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, useId } from 'vue'
import { Info } from '@lucide/vue'
import { getWorkspaceUsage } from '@/apis/workspace_api'
import { formatFileSize } from '@/shared/lib/file_utils'
import { formatFullDateTime } from '@/shared/lib/time'

const usage = ref(null)
const unavailable = ref(false)
const infoId = useId()
const infoOpen = ref(false)
let infoPinned = false
const percent = computed(() =>
  usage.value?.measured_at ? (usage.value.used_bytes / usage.value.limit_bytes) * 100 : null
)
const meterValue = computed(() => Math.min(percent.value ?? 0, 100))
const percentLabel = computed(() => {
  const value = percent.value
  if (value === null) return '—'
  if (value > 0 && value < 1) return '<1%'
  return `${Math.floor(value)}%`
})
const amount = computed(() => percent.value === null
  ? ''
  : `${formatFileSize(usage.value.used_bytes, true)} / ${formatFileSize(usage.value.limit_bytes, true)}`
)
const details = computed(() => {
  const snapshot = usage.value
  if (!snapshot) return [unavailable.value ? '空间用量暂不可用' : '正在读取空间用量']
  if (!snapshot.measured_at && !snapshot.measurement_failed && !unavailable.value) return ['空间用量待统计']
  return [
    snapshot.over_quota && '个人空间已满',
    snapshot.measurement_failed && '最近统计失败',
    unavailable.value && '暂无法刷新空间用量',
    snapshot.measured_at && '统计时间 ' + formatFullDateTime(snapshot.measured_at)
  ].filter(Boolean)
})
let timer
let controller

/** 固定提示时忽略悬浮和聚焦变化。 */
function setInfoOpen(open) {
  if (!infoPinned) infoOpen.value = open
}

/** 点击在固定和关闭提示之间切换。 */
function toggleInfo() {
  infoPinned = !infoPinned
  infoOpen.value = infoPinned
}

/** 关闭提示并清除固定状态。 */
function closeInfo() {
  infoPinned = false
  infoOpen.value = false
}

async function refresh() {
  if (controller) return
  const request = new AbortController()
  controller = request
  const timeout = setTimeout(() => request.abort(), 15000)
  try {
    const snapshot = await getWorkspaceUsage(request.signal)
    if (!request.signal.aborted) {
      usage.value = snapshot
      unavailable.value = false
    }
  } catch {
    if (controller === request) unavailable.value = true
  } finally {
    clearTimeout(timeout)
    if (controller === request) controller = null
  }
}

function start() {
  if (timer) return
  refresh()
  timer = setInterval(refresh, 60000)
}

function stop() {
  closeInfo()
  clearInterval(timer)
  timer = null
  controller?.abort()
  controller = null
}

onMounted(start)
onActivated(start)
onDeactivated(stop)
onUnmounted(stop)
</script>

<style scoped>
.workspace-usage {
  color: var(--gray-700);
  font-size: 12px;
}
.usage-heading {
  display: flex;
  align-items: center;
  gap: 2px;
}
.usage-title {
  color: var(--gray-900);
  font-weight: 600;
}
.usage-info {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
}
.usage-info:hover,
.usage-info:focus-visible {
  color: var(--gray-900);
  background: var(--gray-100);
}
.usage-percent {
  margin-inline-start: auto;
  font-variant-numeric: tabular-nums;
}
.usage-percent.is-full {
  color: var(--color-error-700);
}
.usage-track {
  height: 6px;
  margin-block: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--gray-200);
}
.usage-fill {
  height: 100%;
  min-width: 2px;
  border-radius: inherit;
  background: var(--color-primary-700);
}
.usage-track.is-full .usage-fill {
  background: var(--color-error-700);
}
</style>
