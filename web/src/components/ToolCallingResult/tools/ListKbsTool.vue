<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">{{ operationLabel }}</span>
        <span class="separator">|</span>
        <span class="description">{{ headerSummary }}</span>
      </div>
    </template>
    <template #result="{}">
      <div class="list-kbs-result">
        <div class="kb-list">
          <div v-for="kb in kbList" :key="kb.name" class="kb-item">
            <span class="kb-name">{{ kb.name }}：</span>
            <span class="kb-description">{{ kb.description || '无描述' }}</span>
          </div>
        </div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { getToolCallStatus } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const operationLabel = computed(() => '查看知识库列表')

const parseData = (content) => {
  if (typeof content === 'string') {
    try {
      return JSON.parse(content)
    } catch {
      return []
    }
  }
  return content || []
}

const kbList = computed(() => {
  const resultContent = props.toolCall.tool_call_result?.content
  const data = parseData(resultContent)
  return Array.isArray(data) ? data : []
})

const headerSummary = computed(() => {
  if (getToolCallStatus(props.toolCall) === 'error') return '执行失败'
  const names = kbList.value.map((kb) => kb?.name).filter(Boolean)
  if (!names.length) return '暂无知识库'

  const previewNames = names.slice(0, 3).join('，')
  const remainingCount = names.length - 3
  return remainingCount > 0
    ? `${names.length}个知识库：${previewNames} 等${remainingCount}个`
    : `${names.length}个知识库：${previewNames}`
})
</script>

<style scoped lang="less">
.list-kbs-result {
  padding: 4px;

  .kb-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .kb-item {
    font-size: 12px;
    line-height: 1.6;
    word-break: break-word;

    .kb-name {
      font-weight: 600;
      color: var(--gray-800);
    }

    .kb-description {
      color: var(--gray-600);
    }
  }
}
</style>
