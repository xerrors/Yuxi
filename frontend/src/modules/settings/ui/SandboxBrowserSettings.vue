<template>
  <div class="sandbox-browser" aria-label="沙盒浏览器">
    <div>
      <h4 class="settings-field-label">沙盒浏览器</h4>
      <span class="settings-section-description" role="status">{{ stateLabel }}</span>
      <p v-if="environmentUsable && error" class="sandbox-browser-error" role="alert">{{ error }}</p>
    </div>
    <a-button v-if="connecting || canConnect" size="small" :loading="connecting" :disabled="loading" @click="connect">
      {{ status === 'offline' ? '重新连接' : '连接沙盒浏览器' }}
    </a-button>
    <a-button
      v-else-if="environmentUsable && (readError || status === 'connecting')"
      size="small"
      :loading="loading"
      @click="refresh"
    >
      检查连接
    </a-button>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { devicesApi } from '@/apis/devices_api'

const props = defineProps({
  environment: { type: Object, required: true }
})
const emit = defineEmits(['environment-changed'])
const status = ref(null)
const loading = ref(false)
const connecting = ref(false)
const readError = ref('')
const connectError = ref('')
const controller = new AbortController()
const request = { signal: controller.signal }
const environmentUsable = computed(() => ['not_created', 'running', 'paused'].includes(props.environment.state))
const canConnect = computed(() => environmentUsable.value && ['not_connected', 'offline'].includes(status.value))
const error = computed(() => readError.value || connectError.value)
const stateLabel = computed(() => {
  if (connecting.value) return '正在连接…'
  if (!environmentUsable.value) {
    if (props.environment.rebuild_required) return '恢复运行环境后可连接浏览器'
    return props.environment.state === 'failed' ? '运行环境暂不可达，请刷新状态' : '等待运行环境就绪'
  }
  if (loading.value && status.value == null) return '正在读取状态…'
  const labels = {
    not_connected: props.environment.state === 'not_created' ? '首次连接会创建运行环境' : '未连接',
    offline: '已离线',
    connecting: '正在确认连接',
    ready: '已连接到 aBridge',
    paused: '设备授权已暂停',
    revoked: '设备授权已撤销',
    unavailable: '当前部署未启用沙盒浏览器'
  }
  return labels[status.value] || '状态未确认'
})

async function refresh() {
  if (!environmentUsable.value || connecting.value || loading.value) return
  loading.value = true
  readError.value = ''
  try {
    status.value = (await devicesApi.getSandboxBrowser(request)).status
    if (status.value === 'ready') connectError.value = ''
  } catch (failure) {
    if (controller.signal.aborted) return
    status.value = null
    readError.value = failure.message || '沙盒浏览器状态读取失败'
  } finally {
    loading.value = false
  }
}

async function connect() {
  if (connecting.value || loading.value || !canConnect.value) return
  connecting.value = true
  connectError.value = ''
  try {
    status.value = (await devicesApi.connectSandboxBrowser(request)).status
  } catch (failure) {
    if (controller.signal.aborted) return
    connectError.value = failure.response?.data?.detail?.code === 'sandbox_service_unsupported'
      ? '此环境需要升级浏览器客户端，请联系管理员'
      : failure.message || '连接沙盒浏览器失败'
  } finally {
    connecting.value = false
    if (!controller.signal.aborted) emit('environment-changed')
  }
}

watch(() => props.environment, refresh, { immediate: true })
onUnmounted(() => controller.abort())
</script>

<style scoped>
.sandbox-browser {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 8px 16px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.sandbox-browser h4 {
  margin: 0 0 2px;
}

.sandbox-browser-error {
  margin: 4px 0 0;
  color: var(--color-error-text);
}
</style>
