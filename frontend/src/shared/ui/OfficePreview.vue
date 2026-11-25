<template>
  <div ref="hostRef" class="office-preview-host">
    <div v-if="loadError" class="office-preview-status office-preview-error">
      <CircleAlert :size="22" />
      <span>{{ loadError }}</span>
    </div>
    <div v-else-if="!canRender" class="office-preview-status">
      <LoaderCircle class="office-preview-spinner" :size="20" />
      <span>正在加载 Office 预览...</span>
    </div>
    <component
      :is="viewerComponent"
      v-else
      :key="fileKey"
      ref="viewerRef"
      class="office-preview-frame"
      :file="file"
      :options="viewerOptions"
      @error="onViewerError"
    />
  </div>
</template>

<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef, watch } from 'vue'
import { CircleAlert, LoaderCircle } from '@lucide/vue'
import { useThemeStore } from '@/shared/model/theme'

const props = defineProps({
  file: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['error'])

const themeStore = useThemeStore()
const hostRef = ref(null)
const viewerRef = ref(null)
const viewerComponent = shallowRef(null)
const configuredRenderers = shallowRef(null)
const loadError = ref('')
const deactivated = ref(false)
// 先视为不可见，只有布局给出实际尺寸后才挂载渲染器，避免隐藏面板留下 Worker。
const collapsed = ref(true)
let resizeObserver = null

const fileKey = computed(
  () => `${props.file?.name || ''}:${props.file?.size || 0}:${props.file?.lastModified || 0}`
)
const active = computed(() => !deactivated.value && !collapsed.value)
const canRender = computed(
  () => active.value && viewerComponent.value && configuredRenderers.value && props.file
)
const viewerOptions = computed(() => ({
  rendererMode: 'replace',
  renderers: configuredRenderers.value,
  theme: themeStore.isDark ? 'dark' : 'light',
  styleIsolation: 'shadow',
  toolbar: { position: 'bottom-right' }
}))

const destroyViewer = () => {
  const viewer = viewerRef.value
  viewerRef.value = null
  try {
    viewer?.destroy?.()
  } catch {
    // 组件卸载会再次销毁会话，重复调用保持幂等。
  }
}

const ensureViewer = async () => {
  if (viewerComponent.value && configuredRenderers.value) return
  try {
    // 渲染器与 Worker/WASM/字体由 Vite 插件打进同源产物，这里只在首次打开时加载。
    const [vueModule, , rendererModule] = await Promise.all([
      import('@file-viewer/vue3'),
      import('@file-viewer/vue3/dist/file-viewer3.css'),
      import('virtual:file-viewer-renderers')
    ])
    viewerComponent.value = vueModule.FileViewer || vueModule.default
    configuredRenderers.value = rendererModule.configuredFileViewerRenderers
    if (!viewerComponent.value || !configuredRenderers.value) {
      throw new Error('Office 预览组件未就绪')
    }
  } catch (error) {
    loadError.value = error?.message || 'Office 预览组件加载失败'
    emit('error', loadError.value)
  }
}

const onViewerError = (message) => {
  const text = String(message || '浏览器无法预览该 Office 文件')
  loadError.value = text
  emit('error', text)
}

watch(active, (value) => {
  if (value) ensureViewer()
})

watch(fileKey, () => {
  loadError.value = ''
})

onMounted(() => {
  const node = hostRef.value
  if (!node || typeof ResizeObserver === 'undefined') {
    collapsed.value = false
    ensureViewer()
    return
  }
  resizeObserver = new ResizeObserver((entries) => {
    const box = entries[0]?.contentRect
    collapsed.value = !box || (box.width < 1 && box.height < 1)
  })
  resizeObserver.observe(node)
})

onActivated(() => {
  deactivated.value = false
})

onDeactivated(() => {
  deactivated.value = true
  destroyViewer()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  destroyViewer()
})
</script>

<style scoped>
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

.office-preview-error {
  color: var(--color-error-700);
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
