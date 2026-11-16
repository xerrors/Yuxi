<template>
  <div class="kb-document-preview">
    <div class="document-summary">
      <div class="summary-main">{{ summaryText }}</div>
      <div v-if="result?.file_id" class="summary-meta">file_id: {{ result.file_id }}</div>
    </div>

    <div v-if="documentWindows.length > 0" class="window-list">
      <div
        v-for="(window, windowIndex) in documentWindows"
        :key="windowKey(window, windowIndex)"
        class="window-card"
      >
        <div class="window-title">
          <span>{{ windowTitle(window, windowIndex) }}</span>
          <span v-if="window.matched_lines?.length" class="matched-count">
            命中 {{ window.matched_lines.length }} 行
          </span>
        </div>
        <pre class="content-preview"><span
          v-for="(line, lineIndex) in splitContent(window.content)"
          :key="`${windowKey(window, windowIndex)}-${lineIndex}`"
          class="content-line"
          :class="{ matched: isMatchedLine(line, window.matched_lines) }"
        >{{ line }}</span></pre>
      </div>
    </div>

    <div v-else class="empty-text">暂无可预览内容</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  result: {
    type: Object,
    required: true
  },
  mode: {
    type: String,
    default: 'open'
  }
})

const documentWindows = computed(() => {
  if (Array.isArray(props.result.windows)) return props.result.windows
  if (props.result.content) return [props.result]
  return []
})

const summaryText = computed(() => {
  if (props.mode === 'find') {
    const modeText = props.result.match_mode === 'regex' ? '正则' : '关键词'
    return `${modeText}查找: ${props.result.total_matches || 0} 处匹配，${documentWindows.value.length} 个上下文窗口`
  }

  const startLine = props.result.start_line || 0
  const endLine = props.result.end_line || 0
  const totalLines = props.result.total_lines || 0
  const moreText = props.result.has_more_after ? `，下一段 offset ${props.result.next_offset}` : ''
  return `打开文档: 第 ${startLine}-${endLine} 行 / 共 ${totalLines} 行${moreText}`
})

const splitContent = (content = '') => String(content).split('\n')

const lineNumber = (line) => {
  const match = String(line).match(/^\s*(\d+)\t/)
  return match ? Number(match[1]) : null
}

const isMatchedLine = (line, matchedLines = []) => {
  const number = lineNumber(line)
  return number !== null && matchedLines.includes(number)
}

const windowKey = (window, index) => `${window.start_line || 0}-${window.end_line || 0}-${index}`

const windowTitle = (window, index) => {
  const startLine = window.start_line || 0
  const endLine = window.end_line || 0
  if (startLine || endLine) return `窗口 ${index + 1}: 第 ${startLine}-${endLine} 行`
  return `窗口 ${index + 1}`
}
</script>

<style scoped lang="less">
.kb-document-preview {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  overflow: hidden;

  .document-summary {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    background: var(--gray-25);
    border-bottom: 1px solid var(--gray-100);
    font-size: 12px;
    color: var(--gray-700);

    .summary-main {
      font-weight: 600;
    }

    .summary-meta {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--gray-600);
    }
  }

  .window-list {
    display: flex;
    flex-direction: column;
  }

  .window-card {
    border-bottom: 1px solid var(--gray-100);

    &:last-child {
      border-bottom: none;
    }
  }

  .window-title {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 12px;
    font-size: 12px;
    color: var(--gray-700);
    background: var(--gray-10);

    .matched-count {
      color: var(--main-700);
      white-space: nowrap;
    }
  }

  .content-preview {
    margin: 0;
    padding: 10px 12px;
    max-height: 360px;
    overflow: auto;
    background: var(--gray-0);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .content-line {
    display: block;
    min-height: 1.6em;

    &.matched {
      background: var(--main-50);
      color: var(--main-800);
    }
  }

  .empty-text {
    padding: 14px;
    text-align: center;
    color: var(--gray-600);
    font-size: 12px;
  }
}
</style>
