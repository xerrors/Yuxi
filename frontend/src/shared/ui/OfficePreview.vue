<template>
  <div ref="hostRef" class="office-preview-host">
    <div v-if="!active || !viewer" class="office-preview-status" role="status">
      <LoaderCircle class="office-preview-spinner" :size="20" />
      <span>正在加载 Office 预览...</span>
    </div>
    <component
      :is="viewer.component"
      v-else
      class="office-preview-frame"
      :file="file"
      :options="viewerOptions"
      @error="emit('error', $event)"
    />
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, ref, shallowRef, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { LoaderCircle } from '@lucide/vue'
import { useThemeStore } from '@/shared/model/theme'

defineProps({
  file: { type: File, required: true }
})

const emit = defineEmits(['error'])
const themeStore = useThemeStore()
const hostRef = ref(null)
const viewer = shallowRef(null)
const visible = ref(false)
const deactivated = ref(false)
const active = computed(() => visible.value && !deactivated.value)

const viewerOptions = computed(() => ({
  rendererMode: 'replace',
  renderers: viewer.value?.renderers,
  theme: themeStore.isDark ? 'dark' : 'light',
  locale: 'zh-CN',
  messages: { 'spreadsheet.state.rowsAndColumns': '共 {rows} 行，{cols} 列' },
  styleIsolation: 'shadow',
  toolbar: false
}))

// 隐藏面板和 KeepAlive 停用时卸载 FileViewer，由组件释放自己的 Worker 和渲染资源。
useResizeObserver(hostRef, ([entry]) => {
  visible.value = entry.contentRect.width > 0 && entry.contentRect.height > 0
})
onActivated(() => { deactivated.value = false })
onDeactivated(() => { deactivated.value = true })

watch(active, async (isActive) => {
  if (!isActive || viewer.value) return
  try {
    const [{ FileViewer }, , { configuredFileViewerRenderers }] = await Promise.all([
      import('@file-viewer/vue3'),
      import('@file-viewer/vue3/dist/file-viewer3.css'),
      import('virtual:file-viewer-renderers')
    ])
    viewer.value = { component: FileViewer, renderers: configuredFileViewerRenderers }
  } catch (error) {
    if (active.value) emit('error', error.message || 'Office 预览组件加载失败')
  }
})
</script>

<style scoped>
.office-preview-host {
  isolation: isolate;
}

.office-preview-host,
.office-preview-frame {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.office-preview-status {
  min-height: 260px;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  text-align: center;
  color: var(--gray-500);
  font-size: 14px;
  line-height: 1.6;
}

.office-preview-spinner {
  animation: office-preview-spin 1s linear infinite;
  color: var(--color-primary-500);
}

@keyframes office-preview-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
