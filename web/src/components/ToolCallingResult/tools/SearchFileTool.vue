<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="true">
    <template #header>
      <div class="sep-header">
        <span class="note">搜索知识库文件</span>
        <span class="separator" v-if="kbNameLabel">|</span>
        <span class="description" v-if="kbNameLabel">知识库: {{ kbNameLabel }}</span>
        <span class="separator" v-if="queryLabel">|</span>
        <span class="description" v-if="queryLabel">{{ queryLabel }}</span>
      </div>
    </template>

    <template #result="{ resultContent }">
      <div v-if="isError(resultContent)" class="plain-result">
        {{ stringifyResult(resultContent) }}
      </div>
      <div v-else class="search-file-result">
        <div class="result-count">
          共 {{ resultData(resultContent).total }} 个文件<span
            v-if="resultData(resultContent).has_more"
            class="more-hint"
            >（仅展示前 {{ resultData(resultContent).files.length }} 个）</span
          >
        </div>
        <div v-if="resultData(resultContent).files.length" class="file-list">
          <div
            v-for="file in resultData(resultContent).files"
            :key="file.file_id"
            class="file-item"
            @click="openFileDetail(file)"
          >
            <FileText class="file-icon" :size="12" />
            <span class="file-name" :title="file.filename">{{ file.filename }}</span>
          </div>
        </div>
        <div v-else class="empty-result">未找到匹配的文件</div>
      </div>
      <FileDetailModal v-model:open="modalOpen" :kb-id="selectedKbId" :file-id="selectedFileId" />
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed, ref } from 'vue'
import { FileText } from '@lucide/vue'
import BaseToolCall from '../BaseToolCall.vue'
import FileDetailModal from '@/components/FileDetailModal.vue'
import { parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const args = computed(() => parseToolCallArgs(props.toolCall))

const kbNameLabel = computed(() => args.value.kb_name || '')
const queryLabel = computed(() => args.value.query || '')

const modalOpen = ref(false)
const selectedKbId = ref('')
const selectedFileId = ref('')

const openFileDetail = (file) => {
  selectedKbId.value = file.kb_id || args.value.kb_id || ''
  selectedFileId.value = file.file_id || ''
  modalOpen.value = Boolean(selectedKbId.value && selectedFileId.value)
}

const parseResult = (content) => {
  if (typeof content !== 'string') return content
  try {
    return JSON.parse(content)
  } catch {
    return content
  }
}

// 工具在缺少参数 / 知识库不可见等情况下返回字符串提示，其余返回结构化对象。
const isError = (content) => typeof parseResult(content) !== 'object'

const resultData = (content) => {
  const data = parseResult(content)
  return {
    files: Array.isArray(data?.files) ? data.files : [],
    total: data?.total ?? 0,
    has_more: Boolean(data?.has_more)
  }
}

const stringifyResult = (content) => {
  const result = parseResult(content)
  return typeof result === 'string' ? result : JSON.stringify(result, null, 2)
}
</script>

<style scoped lang="less">
.search-file-result {
  background: var(--gray-0);
  border-radius: 8px;
  padding: 8px;

  .result-count {
    font-size: 11px;
    color: var(--gray-600);
    margin-bottom: 8px;

    .more-hint {
      color: var(--gray-500);
    }
  }

  .file-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .file-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    background: var(--gray-10);
    border-radius: 4px;
    cursor: pointer;
    transition: background 0.15s;

    &:hover {
      background: var(--gray-50);
    }

    .file-icon {
      flex-shrink: 0;
      color: var(--gray-500);
    }

    .file-name {
      font-size: 12px;
      color: var(--gray-700);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  .empty-result {
    font-size: 12px;
    color: var(--gray-600);
    padding: 4px 0;
  }
}

.plain-result {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 10px 12px;
  color: var(--gray-700);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
