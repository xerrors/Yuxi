<template>
  <section class="sandbox-settings settings-section-block" aria-label="运行环境">
    <div class="settings-header sandbox-header">
      <div class="sandbox-heading">
        <h3 class="settings-section-title">运行环境</h3>
      </div>
      <div class="settings-header-actions">
        <a-button
          class="sandbox-icon-button lucide-icon-btn"
          type="text"
          :loading="loading"
          title="刷新环境状态"
          aria-label="刷新环境状态"
          @click="refresh"
        >
          <template #icon><RefreshCw :size="16" /></template>
        </a-button>
        <ActionDropdown v-if="!ephemeral" v-model:open="menuOpen" :width="144" align="end">
          <template #trigger
            ><a-button
              class="sandbox-icon-button lucide-icon-btn"
              type="text"
              :loading="rebuilding"
              aria-label="环境操作"
              aria-haspopup="menu"
              :aria-expanded="menuOpen"
              title="环境操作"
            >
              <template #icon><MoreHorizontal :size="16" /></template> </a-button
          ></template>
          <div role="menu">
            <button
              type="button"
              role="menuitem"
              class="config-dropdown-item sandbox-rebuild"
              :disabled="!status || status.busy || loading || rebuilding"
              :title="status?.busy ? '存在活动任务或待清理操作，暂不可重建' : undefined"
              @click="confirmRebuild"
            >
              重建环境
            </button>
          </div>
        </ActionDropdown>
      </div>
    </div>

    <div class="sandbox-overview">
      <div
        class="sandbox-state-row"
        aria-label="环境状态"
        :title="ephemeral ? '每个任务独立临时环境，任务结束后销毁' : '所有对话共享此环境'"
      >
        <div class="sandbox-state-icon">
          <component :is="statePresentation.icon" :size="14" aria-hidden="true" />
        </div>
        <div class="sandbox-state-content" :title="stateTitle">
          <span
            class="sandbox-state-value"
            :class="`is-${statePresentation.tone}`"
            aria-live="polite"
            >{{ statePresentation.label }}</span
          >
          <div class="sandbox-state-meta">
            <span v-if="timingText" class="sandbox-timing">{{ timingText }}</span>

            <span
              v-if="ephemeral"
              class="sandbox-scope"
              :title="ephemeral ? '每个任务独立临时环境，任务结束后销毁' : '所有对话共享此环境'"
              >{{ ephemeral ? '任务独立' : '所有对话共享' }}</span
            >
          </div>
        </div>
      </div>
      <div
        v-if="!ephemeral"
        class="sandbox-disk"
        :class="{ 'is-unqueried': !diskUsage }"
        title="系统和主目录所在磁盘，不含共享工作区"
      >
        <div class="sandbox-disk-heading">
          <span class="sandbox-label">磁盘</span>
          <div v-if="diskUsage" class="sandbox-disk-content">
            <span v-if="diskUsage" title="上次手动查询的结果"
              >已用 {{ formatFileSize(diskUsage.used_bytes) }} · 可用
              {{ formatFileSize(diskUsage.available_bytes) }}</span
            >
          </div>
          <a-button
            v-if="!diskUsage"
            class="sandbox-disk-action"
            type="link"
            :loading="diskLoading"
            :disabled="!['running', 'paused'].includes(status?.state)"
            :aria-label="diskActionLabel"
            :title="diskActionLabel"
            @click="readDiskUsage"
          >
            查看用量
          </a-button>
        </div>
        <div
          v-if="diskPercent !== null"
          class="sandbox-disk-meter"
          role="meter"
          aria-label="磁盘用量"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="diskPercent"
          :aria-valuetext="`已用 ${formatFileSize(diskUsage.used_bytes)}，可用 ${formatFileSize(diskUsage.available_bytes)}`"
        >
          <div class="sandbox-disk-fill" :style="{ width: `${diskPercent}%` }" />
        </div>
      </div>
    </div>
    <p v-if="diskError" class="settings-section-description" role="alert">{{ diskError }}</p>
    <a-alert
      v-if="error || failedGeneration"
      type="error"
      :message="error || '运行环境异常'"
      :description="failedGeneration ? '可在「环境操作」菜单中重建环境。' : undefined"
      show-icon
    />
    <ul v-if="status?.active_runs?.length" class="sandbox-runs">
      <li v-for="run in status.active_runs" :key="run.id">
        <span>{{ run.status === 'cancel_requested' ? '取消处理中' : '任务处理中' }}</span>
        <RouterLink
          target="_blank"
          rel="noopener noreferrer"
          :to="{ name: 'AgentCompWithThreadId', params: { thread_id: run.thread_id } }"
          >查看对话</RouterLink
        >
      </li>
    </ul>
    <p v-if="status?.active_operations" class="settings-section-description">
      活动操作及待清理命令：{{ status.active_operations }} 项。
    </p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import {
  CircleAlert,
  CircleDashed,
  CircleHelp,
  CirclePlay,
  LoaderCircle,
  Moon,
  MoreHorizontal,
  RefreshCw
} from '@lucide/vue'
import { sandboxApi } from '@/apis/sandbox_api'
import { formatSandboxDuration, sandboxTimingText } from '../model/sandboxTiming'
import { formatFileSize } from '@/shared/lib/file_utils'
import { formatFullDateTime } from '@/shared/lib/time'
import ActionDropdown from '@/shared/ui/ActionDropdown.vue'

const status = ref(null)
const error = ref('')
const loading = ref(false)
const rebuilding = ref(false)
const menuOpen = ref(false)
const diskUsage = ref(null)
const diskPercent = computed(() => {
  if (!diskUsage.value) return null
  const { used_bytes: used, available_bytes: available } = diskUsage.value
  const capacity = used + available
  return capacity > 0 ? (used / capacity) * 100 : null
})
const diskLoading = ref(false)
const diskError = ref('')
let diskVersion = 0
const clock = ref(Date.now())
const receivedAt = ref(clock.value)
let clockTimer
let refreshTimer
let refreshVersion = 0
const states = {
  not_created: { label: '未创建', icon: CircleDashed, tone: 'muted' },
  running: { label: '运行中', icon: CirclePlay, tone: 'success' },
  paused: { label: '已休眠', icon: Moon, tone: 'muted' },
  creating: { label: '创建中', icon: LoaderCircle, tone: 'pending' },
  rebuilding: { label: '重建中', icon: LoaderCircle, tone: 'pending' },
  resuming: { label: '恢复中', icon: LoaderCircle, tone: 'pending' },
  pausing: { label: '休眠处理中', icon: LoaderCircle, tone: 'pending' },
  failed: { label: '环境异常', icon: CircleAlert, tone: 'error' }
}
const statePresentation = computed(
  () =>
    states[status.value?.state] ||
    (loading.value
      ? { label: '读取中', icon: LoaderCircle, tone: 'muted' }
      : { label: '状态未知', icon: CircleHelp, tone: 'muted' })
)
const ephemeral = computed(() => status.value?.backend === 'container')
const failedGeneration = computed(() =>
  status.value?.rebuild_required ? status.value.generation : undefined
)
const diskActionLabel = computed(() => {
  return status.value?.state === 'paused' ? '唤醒并查询磁盘用量' : '查看磁盘用量'
})
const stateTitle = computed(() => {
  const value = status.value
  if (ephemeral.value) return '任务首次执行命令时创建，任务结束后销毁'
  if (value?.state === 'paused' && value.paused_at)
    return `休眠于 ${formatFullDateTime(value.paused_at)}`
  if (value?.state === 'not_created') return '首次执行时自动创建'
  return value
    ? `空闲 ${formatSandboxDuration(value.idle_timeout_seconds)}自动休眠，执行时自动恢复`
    : ''
})
const timingText = computed(() => {
  if (ephemeral.value) return ''
  const serverTime = Date.parse(status.value?.server_time)
  return sandboxTimingText(status.value, serverTime + clock.value - receivedAt.value)
})

function updateStatus(value) {
  if (status.value && value.generation !== status.value.generation) {
    clearDiskUsage()
  }
  receivedAt.value = clock.value = Date.now()
  status.value = value
  error.value = value.error || ''
}

function clearDiskUsage() {
  diskVersion++
  diskUsage.value = null
  diskLoading.value = false
  diskError.value = ''
}

async function refresh() {
  const version = ++refreshVersion
  loading.value = true
  error.value = ''
  try {
    const value = await sandboxApi.get()
    if (version === refreshVersion) updateStatus(value)
  } catch (failure) {
    if (version === refreshVersion) {
      status.value = null
      clearDiskUsage()
      error.value = failure.message || '环境状态读取失败'
    }
  } finally {
    if (version === refreshVersion) loading.value = false
  }
}

async function readDiskUsage() {
  const version = ++diskVersion
  diskLoading.value = true
  diskError.value = ''
  try {
    const value = await sandboxApi.diskUsage()
    if (version === diskVersion) diskUsage.value = value
  } catch (failure) {
    if (version === diskVersion) {
      diskUsage.value = null
      diskError.value = failure.message || '磁盘用量读取失败，请重试'
    }
  } finally {
    if (version === diskVersion) {
      diskLoading.value = false
      await refresh()
    }
  }
}

async function confirmRebuild() {
  const displayedFailure = failedGeneration.value
  menuOpen.value = false
  await refresh()
  if (loading.value || !status.value || status.value.busy) return
  const generation = failedGeneration.value
  if (displayedFailure !== undefined && displayedFailure !== generation) return
  Modal.confirm({
    title: '确认重建运行环境',
    content: '重建将清除已安装软件及用户主目录中的文件与配置。工作区文件与技能将保留。',
    okText: '确认重建',
    cancelText: '取消',
    okButtonProps: { danger: true },
    async onOk() {
      rebuilding.value = true
      try {
        const value = await sandboxApi.rebuild(generation)
        refreshVersion++
        loading.value = false
        updateStatus(value)
        message.success('运行环境重建完成')
      } catch (failure) {
        message.error(failure.message || '环境重建失败，请刷新状态后重试')
        await refresh()
      } finally {
        rebuilding.value = false
      }
    }
  })
}

onMounted(() => {
  refresh()
  clockTimer = setInterval(() => {
    clock.value = Date.now()
  }, 1000)
  refreshTimer = setInterval(() => {
    if (!loading.value && !rebuilding.value) refresh()
  }, 15000)
})
onUnmounted(() => {
  refreshVersion++
  diskVersion++
  clearInterval(clockTimer)
  clearInterval(refreshTimer)
})
defineExpose({ refresh })
</script>

<style scoped>
.sandbox-settings {
  container: sandbox-overview / inline-size;
  padding-bottom: 20px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
}
.sandbox-settings .sandbox-header {
  flex-direction: row;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.sandbox-heading .settings-section-title {
  margin: 0;
  font-size: 20px;
}
.sandbox-header .settings-header-actions {
  margin-left: auto;
  justify-content: flex-end;
  flex-wrap: nowrap;
}
.sandbox-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px 24px;
}
.sandbox-state-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.sandbox-state-icon {
  display: inline-flex;
  color: var(--color-text-secondary);
  flex-shrink: 0;
}
.sandbox-state-content {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0 12px;
  min-width: 0;
  line-height: 22px;
}
.sandbox-state-value {
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
}
.sandbox-state-value.is-success {
  color: var(--color-success-900);
}
.sandbox-state-value.is-pending {
  color: var(--color-info-900);
}
.sandbox-state-value.is-error {
  color: var(--color-error-700);
}
.sandbox-state-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 20px;
}
.sandbox-disk {
  flex: 0 0 280px;
  min-width: 0;
}
.sandbox-disk.is-unqueried {
  flex-basis: auto;
}
.sandbox-disk-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.sandbox-label,
.sandbox-muted {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.sandbox-disk-meter {
  height: 4px;
  margin-top: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--gray-200);
}
.sandbox-disk-fill {
  height: 100%;
  border-radius: inherit;
  background: var(--main-color);
}
.sandbox-disk-content {
  color: var(--color-text);
  font-size: 12px;
  line-height: 20px;
}
.sandbox-disk-action {
  flex-shrink: 0;
  height: 24px;
  padding: 0;
  font-size: 12px;
}
.sandbox-icon-button {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
}
.sandbox-rebuild {
  color: var(--color-error-700);
}
.sandbox-rebuild:disabled {
  color: var(--color-text-secondary);
  cursor: not-allowed;
}
.sandbox-settings > :deep(.ant-alert),
.sandbox-settings > .settings-section-description,
.sandbox-runs {
  margin: 12px 0 0;
}
.sandbox-runs {
  padding: 0;
  list-style: none;
  font-size: 12px;
}
.sandbox-runs li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
}
@container sandbox-overview (max-width: 600px) {
  .sandbox-overview {
    flex-wrap: wrap;
  }
  .sandbox-disk {
    flex: 0 1 280px;
    margin-left: auto;
  }
}
</style>
