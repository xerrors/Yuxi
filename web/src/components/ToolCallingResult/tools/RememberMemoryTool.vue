<template>
  <BaseToolCall
    :tool-call="toolCall"
    :force-show-result="resultView.hasResult"
    :status="resultView.status === 'error' ? 'error' : ''"
  >
    <template #header>
      <div class="sep-header">
        <span class="note">更新记忆</span>
        <span class="separator" v-if="argsView.contentPreview">|</span>
        <span class="description">{{ argsView.contentPreview }}</span>
        <span class="tag success" v-if="argsView.isReplace">纠正</span>
        <span class="tag" v-if="resultView.lineTag">{{ resultView.lineTag }}</span>
      </div>
    </template>

    <template #result>
      <div class="memory-result">
        <div class="memory-status" :class="{ error: resultView.status === 'error' }">
          {{ resultView.text }}
        </div>
        <div class="memory-meta" v-if="resultView.meta">{{ resultView.meta }}</div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallArgs, parseToolCallResult } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const argsView = computed(() => {
  const args = parseToolCallArgs(props.toolCall)
  const content = String(args.content || '')
    .replace(/\s+/g, ' ')
    .trim()
  return {
    contentPreview: content.length > 60 ? `${content.slice(0, 60)}…` : content,
    isReplace: Boolean(args.replaces)
  }
})

const resultView = computed(() => {
  const result = parseToolCallResult(props.toolCall)
  const status = result?.status || ''
  let text = ''
  if (status === 'updated') {
    text = argsView.value.isReplace ? '记忆已纠正更新' : '记忆已追加到末尾'
  } else if (status === 'unchanged') {
    text = '记忆内容已存在，未变更'
  } else if (status === 'error') {
    text = `记忆更新失败：${result?.error || '未知错误'}`
  }

  if (!result || status === 'error') {
    return { status, text, lineTag: '', meta: '', hasResult: Boolean(result) }
  }

  const start = result.start_line
  const end = result.end_line
  const lineTag = start ? (start === end ? `L${start}` : `L${start}-${end}`) : ''
  const parts = []
  if (result.path) parts.push(result.path)
  if (start && end) {
    parts.push(start === end ? `第 ${start} 行` : `第 ${start}-${end} 行`)
  }
  if (typeof result.size === 'number') parts.push(`${result.size} B`)
  return { status, text, lineTag, meta: parts.join(' · '), hasResult: true }
})
</script>

<style lang="less" scoped>
.memory-result {
  padding: 8px 12px;

  .memory-status {
    font-size: 13px;
    color: var(--gray-700);

    &.error {
      color: var(--color-error-500);
    }
  }

  .memory-meta {
    margin-top: 2px;
    font-size: 12px;
    color: var(--gray-400);
  }
}
</style>
