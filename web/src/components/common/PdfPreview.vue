<template>
  <div class="pdf-preview-container" ref="containerRef">
    <!-- 加载中状态 -->
    <div v-if="loading" class="pdf-status-view">
      <LoaderCircle class="pdf-spinner" :size="22" />
      <span class="pdf-status-text">正在加载 PDF 文档...</span>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="pdf-status-view pdf-error-view">
      <CircleAlert :size="24" class="pdf-error-icon" />
      <span class="pdf-status-text">{{ error }}</span>
    </div>

    <!-- PDF 页面列表 -->
    <div v-else class="pdf-pages-scroll-wrapper">
      <div
        v-for="pageNum in totalPages"
        :key="pageNum"
        class="pdf-page-card"
        :data-page-number="pageNum"
      >
        <canvas :ref="(el) => setCanvasRef(el, pageNum)" class="pdf-canvas" />
        <div class="pdf-page-number-tag">{{ pageNum }} / {{ totalPages }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CircleAlert, LoaderCircle } from '@lucide/vue'

const props = defineProps({
  url: {
    type: String,
    required: true
  },
  // 左右保留的安全内边距（px）
  horizontalPadding: {
    type: Number,
    default: 32
  }
})

const containerRef = ref(null)
const loading = ref(true)
const error = ref('')
const totalPages = ref(0)

const canvasMap = new Map()
const renderTasks = new Map()
let pdfjsLibInstance = null
let currentPdfDoc = null
let currentLoadingTask = null
let resizeObserver = null
let resizeTimer = null
let lastRenderedWidth = 0

const setCanvasRef = (el, pageNum) => {
  if (el) {
    canvasMap.set(pageNum, el)
  } else {
    canvasMap.delete(pageNum)
  }
}

const cancelOngoingRenders = () => {
  for (const [, task] of renderTasks) {
    try {
      task.cancel()
    } catch {
      // 忽略已完成或已取消异常
    }
  }
  renderTasks.clear()
}

const getPdfjs = async () => {
  if (pdfjsLibInstance) return pdfjsLibInstance

  const [pdfjs, workerUrlModule] = await Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url')
  ])

  pdfjs.GlobalWorkerOptions.workerSrc = workerUrlModule.default || workerUrlModule
  pdfjsLibInstance = pdfjs
  return pdfjs
}

// 获取容器可用的实际显示宽度
const getAvailableContainerWidth = () => {
  const container = containerRef.value
  if (!container) {
    return typeof window !== 'undefined' ? window.innerWidth : 800
  }

  // 优先取容器自身或其滚动父容器的实际 clientWidth
  const width =
    container.clientWidth ||
    container.parentElement?.clientWidth ||
    container.getBoundingClientRect().width ||
    800

  return width
}

// 计算当前容器适合的横向 100% 缩放比例
const calculateFitScale = (page) => {
  const containerWidth = getAvailableContainerWidth()
  const availableWidth = Math.max(containerWidth - props.horizontalPadding, 120)
  const unscaledViewport = page.getViewport({ scale: 1.0 })
  return availableWidth / unscaledViewport.width
}

const renderSinglePage = async (pdfDoc, pageNum) => {
  const canvas = canvasMap.get(pageNum)
  if (!canvas || !pdfDoc) return

  try {
    const page = await pdfDoc.getPage(pageNum)
    const fitScale = calculateFitScale(page)
    const viewport = page.getViewport({ scale: fitScale })

    // 支持 HiDPI / Retina 屏幕的高清绘制
    const outputScale = window.devicePixelRatio || 1
    const ctx = canvas.getContext('2d')

    canvas.width = Math.floor(viewport.width * outputScale)
    canvas.height = Math.floor(viewport.height * outputScale)

    // CSS 保持视口物理显示尺寸
    canvas.style.width = `${Math.floor(viewport.width)}px`
    canvas.style.height = `${Math.floor(viewport.height)}px`

    const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null

    // 取消同一页之前未完成的渲染
    if (renderTasks.has(pageNum)) {
      try {
        renderTasks.get(pageNum).cancel()
      } catch {
        // 忽略
      }
      renderTasks.delete(pageNum)
    }

    const renderTask = page.render({
      canvasContext: ctx,
      transform,
      viewport
    })

    renderTasks.set(pageNum, renderTask)
    await renderTask.promise
    renderTasks.delete(pageNum)
  } catch (err) {
    if (err?.name !== 'RenderingCancelledException') {
      console.error(`渲染 PDF 第 ${pageNum} 页失败:`, err)
    }
  }
}

const renderAllPages = async () => {
  if (!currentPdfDoc) return
  cancelOngoingRenders()

  lastRenderedWidth = getAvailableContainerWidth()

  for (let i = 1; i <= totalPages.value; i++) {
    await renderSinglePage(currentPdfDoc, i)
  }
}

const loadPdf = async () => {
  if (!props.url) {
    loading.value = false
    error.value = '无效的 PDF 链接'
    return
  }

  loading.value = true
  error.value = ''
  totalPages.value = 0
  cancelOngoingRenders()

  if (currentLoadingTask) {
    try {
      currentLoadingTask.destroy()
    } catch {
      // 忽略
    }
    currentLoadingTask = null
  }

  try {
    const pdfjs = await getPdfjs()
    const loadingTask = pdfjs.getDocument({
      url: props.url,
      cMapUrl: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@' + pdfjs.version + '/cmaps/',
      cMapPacked: true
    })
    currentLoadingTask = loadingTask

    const pdfDoc = await loadingTask.promise
    currentPdfDoc = pdfDoc
    totalPages.value = pdfDoc.numPages
    loading.value = false

    await nextTick()
    await renderAllPages()
  } catch (err) {
    if (err?.name !== 'RenderingCancelledException') {
      console.error('加载 PDF 失败:', err)
      error.value = '无法加载 PDF 文件或文件格式受损'
    }
    loading.value = false
  }
}

const handleResize = () => {
  if (!currentPdfDoc || loading.value || totalPages.value === 0) return

  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    const currentWidth = getAvailableContainerWidth()
    // 当容器宽度变化超过阈值时自适应重绘
    if (Math.abs(currentWidth - lastRenderedWidth) > 8) {
      renderAllPages()
    }
  }, 120)
}

watch(
  () => props.url,
  () => {
    loadPdf()
  }
)

onMounted(() => {
  loadPdf()

  if (typeof ResizeObserver !== 'undefined' && containerRef.value) {
    resizeObserver = new ResizeObserver(handleResize)
    // 监听父级或自身的变化
    const target = containerRef.value.parentElement || containerRef.value
    resizeObserver.observe(target)
  }
})

onBeforeUnmount(() => {
  clearTimeout(resizeTimer)
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  cancelOngoingRenders()
  if (currentLoadingTask) {
    try {
      currentLoadingTask.destroy()
    } catch {
      // 忽略
    }
  }
  currentPdfDoc = null
  canvasMap.clear()
})
</script>

<style scoped>
.pdf-preview-container {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: 100%;
  background: var(--gray-50, #f8fafc);
  position: relative;
  user-select: text;
  box-sizing: border-box;
}

.pdf-status-view {
  flex: 1;
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px;
  color: var(--gray-600, #64748b);
}

.pdf-spinner {
  animation: pdf-spin 1s linear infinite;
  color: var(--primary-color, #2563eb);
}

.pdf-error-view {
  color: var(--color-danger, #ef4444);
}

.pdf-error-icon {
  opacity: 0.85;
}

.pdf-status-text {
  font-size: 13px;
}

.pdf-pages-scroll-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 24px;
  padding: 16px 12px 28px;
  width: 100%;
  box-sizing: border-box;
}

.pdf-page-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 100%;
  box-shadow:
    0 4px 14px rgba(0, 0, 0, 0.07),
    0 1px 3px rgba(0, 0, 0, 0.03);
  background: #ffffff;
  border-radius: 4px;
  transition: box-shadow 0.2s ease;
}

.pdf-page-card:hover {
  box-shadow:
    0 6px 18px rgba(0, 0, 0, 0.1),
    0 2px 5px rgba(0, 0, 0, 0.05);
}

.pdf-canvas {
  display: block;
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  background: #ffffff;
}

.pdf-page-number-tag {
  position: absolute;
  bottom: -18px;
  right: 2px;
  font-size: 11px;
  color: var(--gray-400, #94a3b8);
  user-select: none;
}

@keyframes pdf-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
