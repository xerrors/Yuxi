<template>
  <div class="markdown-content-viewer">
    <div class="viewer-header">
      <h4>文件内容</h4>
      <div class="header-controls">
        <div class="header-info">
          <span v-if="mappedChunks.length > 0">共 {{ mappedChunks.length }} 个片段</span>
          <span>总长度 {{ formatTextLength(mergedContent.length) }}</span>
        </div>
        <button
          class="toggle-btn"
          v-if="mappedChunks.length > 0"
          @click="toggleChunkPanel"
          :title="chunkPanelVisible ? '隐藏片段列表' : '显示片段列表'"
        >
          <ChevronLeft v-if="chunkPanelVisible" :size="14" />
          <ChevronRight v-else :size="14" />
        </button>
      </div>
    </div>

    <div class="viewer-container">
      <!-- 左侧：Markdown内容 -->
      <div class="content-panel">
        <MarkdownPreview :content="mergedContent" class="markdown-content" />
      </div>

      <!-- 右侧：Chunk信息 -->
      <div class="chunk-panel" v-show="chunkPanelVisible">
        <div class="chunk-list">
          <div
            v-for="(chunk, index) in mappedChunks"
            :key="chunk.id"
            class="chunk-item"
            :class="{
              active: activeChunkIndex === index,
              highlighted: highlightedChunkIndex === index
            }"
            @click="handleChunkClick(index)"
            @mouseenter="highlightChunk(index)"
            @mouseleave="unhighlightChunk()"
          >
            <div class="chunk-header">
              <span class="chunk-order">#{{ chunk.chunk_order_index }}</span>
              <span class="chunk-id">{{ chunk.id }}</span>
            </div>
            <div class="chunk-meta">
              <span class="chunk-length">{{ formatTextLength(chunk.content.length) }}</span>
              <span class="chunk-range">{{ chunk.startOffset }}-{{ chunk.endOffset }}</span>
            </div>
            <div class="chunk-preview">{{ getChunkPreview(chunk.content) }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 悬浮提示 -->
    <div v-if="showTooltip && currentChunk" class="chunk-tooltip" :style="tooltipStyle">
      <div class="tooltip-header">
        <strong>片段信息</strong>
      </div>
      <div class="tooltip-content">
        <div class="tooltip-row">
          <span class="label">ID:</span>
          <span class="value">{{ currentChunk.id }}</span>
        </div>
        <div class="tooltip-row">
          <span class="label">序号:</span>
          <span class="value">{{ currentChunk.chunk_order_index }}</span>
        </div>
        <div class="tooltip-row">
          <span class="label">位置:</span>
          <span class="value">{{ currentChunk.startOffset }} - {{ currentChunk.endOffset }}</span>
        </div>
        <div class="tooltip-row">
          <span class="label">长度:</span>
          <span class="value">{{ formatTextLength(currentChunk.content.length) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { mergeChunks, getChunkPreview } from '@/utils/chunkUtils'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { ChevronRight, ChevronLeft } from 'lucide-vue-next'

const props = defineProps({
  chunks: {
    type: Array,
    default: () => []
  },
  content: {
    type: String,
    default: ''
  }
})

// 响应式引用
const showTooltip = ref(false)
const currentChunk = ref(null)
const tooltipStyle = ref({ top: '0px', left: '0px' })
const activeChunkIndex = ref(null)
const highlightedChunkIndex = ref(null)
const chunkPanelVisible = ref(false)

// 合并chunks
const mergeResult = computed(() => mergeChunks(props.chunks))
const mergedContent = computed(() => props.content || mergeResult.value.content)
const mappedChunks = computed(() => mergeResult.value.chunks)

// 格式化文本长度
function formatTextLength(length) {
  if (!length && length !== 0) return '0 字符'

  if (length < 1000) {
    return `${length} 字符`
  } else {
    return `${(length / 1000).toFixed(1)}k 字符`
  }
}

// 高亮chunk
function highlightChunk(index) {
  if (!mappedChunks.value?.[index]) return
  highlightedChunkIndex.value = index
  currentChunk.value = mappedChunks.value[index]
}

function unhighlightChunk() {
  highlightedChunkIndex.value = null
  currentChunk.value = null
}

// 处理chunk点击
function handleChunkClick(index) {
  if (!mappedChunks.value?.[index]) return
  activeChunkIndex.value = index
}

// 切换chunk面板显示
function toggleChunkPanel() {
  chunkPanelVisible.value = !chunkPanelVisible.value
}

// 处理鼠标移动显示tooltip
function handleMouseMove(event) {
  if (!currentChunk.value) return

  tooltipStyle.value = {
    top: event.clientY + 10 + 'px',
    left: event.clientX + 10 + 'px'
  }
}

// 生命周期
onMounted(() => {
  document.addEventListener('mousemove', handleMouseMove)
})

onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove)
})
</script>

<style scoped>
.markdown-content-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  overflow: hidden;
}

.viewer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 16px;
  background: var(--gray-25);
  border-bottom: 1px solid var(--gray-200);
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 16px;
}

.toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gray-100);
  border: none;
  border-radius: 4px;
  width: 24px;
  height: 24px;
  padding: 0;
  cursor: pointer;
  transition: all 0.2s ease;
  color: var(--gray-700);
}

.toggle-btn:hover {
  background: var(--gray-200);
}

.viewer-header h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.header-info {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--gray-500);
}

.viewer-container {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.content-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: var(--gray-0);
  border-right: 1px solid var(--gray-200);
  min-height: 0; /* 关键：确保flex项目可以缩小 */
}

.markdown-content {
  min-height: 100%;
  overflow: visible;
}

.chunk-panel {
  width: 300px;
  overflow-y: auto;
  background: var(--gray-25);
  padding: 16px;
  min-height: 0; /* 确保flex项目可以缩小 */
}

.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chunk-item {
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  padding: 12px;
  /* cursor: pointer; */
  /* transition: all 0.2s ease; */
  /* user-select: none; */
}

.chunk-item:hover {
  border-color: var(--main-color);
  box-shadow: 0 2px 8px rgba(1, 97, 121, 0.1);
}

/* .chunk-item.active {
  border-color: var(--main-color);
  background: var(--main-50);
  box-shadow: 0 2px 8px rgba(1, 97, 121, 0.2);
} */
/*
.chunk-item.highlighted {
  border-color: var(--color-success-500);
  background: var(--main-50);
} */

.chunk-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.chunk-order {
  font-weight: 600;
  color: var(--main-color);
  font-size: 12px;
}

.chunk-id {
  font-size: 11px;
  color: var(--gray-500);
  font-family: monospace;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-meta {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 11px;
  color: var(--gray-600);
}

.chunk-preview {
  font-size: 11px;
  color: var(--gray-500);
  line-height: 1.4;
  /* max-height: 40px; */
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  line-clamp: 4;
}

.chunk-tooltip {
  position: fixed;
  background: var(--gray-0);
  border: 1px solid var(--gray-300);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  padding: 12px;
  z-index: 1000;
  min-width: 250px;
  max-width: 350px;
  pointer-events: none;
}

.tooltip-header {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--gray-200);
}

.tooltip-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tooltip-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.tooltip-row .label {
  font-weight: 500;
  color: var(--gray-600);
  min-width: 50px;
  flex-shrink: 0;
}

.tooltip-row .value {
  color: var(--gray-1000);
  flex: 1;
  word-break: break-all;
}
</style>
