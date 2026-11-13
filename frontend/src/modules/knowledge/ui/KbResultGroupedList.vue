<template>
  <div class="kb-result-grouped-list">
    <div v-if="showSummary" class="result-summary">
      找到 {{ normalizedChunks.length }} 个相关文档片段，来自 {{ fileGroupList.length }} 个文件
    </div>

    <div class="kb-results" v-if="normalizedChunks.length > 0">
      <div v-for="fileGroup in fileGroupList" :key="fileGroup.key" class="file-group-item">
        <button
          class="file-info"
          :aria-label="`查看 ${fileGroup.filename} 的检索片段`"
          @click="openFileChunksModal(fileGroup)"
        >
          <FileText :size="15" class="file-icon" />
          <span class="file-name" :title="fileGroup.filename">{{ fileGroup.filename }}</span>
          <span class="chunk-count">{{ fileGroup.chunks.length }} 个片段</span>
        </button>
        <div class="file-actions">
          <button
            v-if="canReadDocument(fileGroup)"
            class="view-file-btn"
            @click.stop="openDocument(fileGroup)"
            title="查看完整文件"
            aria-label="查看完整文件"
          >
            <Eye :size="14" />
          </button>
        </div>
      </div>
    </div>

    <div v-else class="no-results">
      <p>{{ emptyText }}</p>
    </div>

    <KbFileChunksModal
      v-model:open="chunksModalVisible"
      :file-group="selectedFileGroup"
      @open-document="openDocument(selectedFileGroup, $event)"
    />

    <FileDetailModal
      v-if="useFileDetail"
      v-model:open="documentOpen"
      :kb-id="selectedDocument.kb_id"
      :file-id="selectedDocument.file_id"
      :chunk-id="selectedDocument.chunk_id"
      :start-line="selectedDocument.start_line"
      :end-line="selectedDocument.end_line"
    />
    <a-modal v-else v-model:open="documentOpen" :title="selectedDocument?.title" :footer="null" :width="900" destroy-on-close>
      <ExternalDocumentReader
        v-if="documentOpen && selectedDocument"
        :kb-id="selectedDocument.kb_id"
        :file-id="selectedDocument.file_id"
        :title="selectedDocument.title"
        :start-line="selectedDocument.start_line"
        :end-line="selectedDocument.end_line"
        @close="documentOpen = false"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { FileText, Eye } from '@lucide/vue'
import KbFileChunksModal from './KbFileChunksModal.vue'
import ExternalDocumentReader from '@/modules/knowledge/ui/ExternalDocumentReader.vue'
import FileDetailModal from '@/modules/knowledge/ui/FileDetailModal.vue'
import { groupKnowledgeChunks } from '@/modules/knowledge/model/kbResultGroups.js'
import { useKnowledgeBaseStore } from '@/modules/knowledge/model/knowledgeBase'
import { useUserStore } from '@/modules/identity'

const props = defineProps({
  chunks: {
    type: [Array, Object],
    default: () => []
  },
  showSummary: {
    type: Boolean,
    default: true
  },
  emptyText: {
    type: String,
    default: '未找到相关知识库内容'
  }
})

const chunksModalVisible = ref(false)
const selectedFileGroup = ref(null)
const documentOpen = ref(false)
const selectedDocument = ref(null)
const knowledgeBaseStore = useKnowledgeBaseStore()
const userStore = useUserStore()
// 文件详情使用管理侧内容接口；普通用户沿用与回答引用相同的只读窗口。
const useFileDetail = computed(() => Boolean(selectedDocument.value?.supports_documents && userStore.isAdmin))

const resolveChunks = (input) => {
  if (Array.isArray(input)) return input
  if (!input || typeof input !== 'object') return []

  if (Array.isArray(input.results)) return input.results
  if (Array.isArray(input.chunks)) return input.chunks
  if (Array.isArray(input.data?.chunks)) return input.data.chunks

  return []
}

const normalizedChunks = computed(() =>
  resolveChunks(props.chunks)
    .filter((item) => item && typeof item === 'object' && item.content)
    .map((item) => {
      const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {}
      const kb = knowledgeBaseStore.knowledgeBases.find((kb) => kb.kb_id === item.kb_id)
        || (knowledgeBaseStore.knowledgeBase?.kb_id === item.kb_id ? knowledgeBaseStore.knowledgeBase : null)
      const source =
        metadata.source ||
        metadata.file_name ||
        metadata.filename ||
        metadata.title ||
        item.file_name ||
        item.filename ||
        item.file_id ||
        item.kb_id ||
        '未知来源'

      return {
        ...item,
        file_id: item.file_id || metadata.file_id || '',
        supports_document_read: item.supports_document_read ?? kb?.supports_document_read ?? false,
        supports_documents: item.supports_documents ?? kb?.supports_documents ?? false,
        score: typeof item.score === 'number' ? item.score : metadata.score,
        rerank_score:
          typeof item.rerank_score === 'number' ? item.rerank_score : metadata.rerank_score,
        metadata: {
          ...metadata,
          source,
          chunk_id: metadata.chunk_id || item.id
        }
      }
    })
)

const fileGroupList = computed(() => {
  return groupKnowledgeChunks(normalizedChunks.value)
})

const openFileChunksModal = (fileGroup) => {
  selectedFileGroup.value = fileGroup
  chunksModalVisible.value = true
}

/** 原文读取需要明确能力与文件身份；后端在每次请求继续执行权限校验。 */
const canReadDocument = (fileGroup) => Boolean(
  fileGroup?.kb_id && fileGroup.file_id && fileGroup.chunks.some((chunk) => chunk.supports_document_read)
)

/** 使用命中自身的库和行号打开既有阅读器，避免同名跨库来源串用。 */
const openDocument = (fileGroup, chunk = null) => {
  if (!canReadDocument(fileGroup)) return
  selectedDocument.value = {
    kb_id: fileGroup.kb_id,
    file_id: fileGroup.file_id,
    title: fileGroup.filename,
    supports_documents: chunk?.supports_documents ?? fileGroup.chunks[0].supports_documents,
    chunk_id: chunk?.metadata?.chunk_id || '',
    start_line: chunk?.metadata?.start_line || null,
    end_line: chunk?.metadata?.end_line || null
  }
  chunksModalVisible.value = false
  documentOpen.value = true
}
</script>

<style scoped lang="less">
.kb-result-grouped-list {
  padding: 4px;
  .result-summary {
    padding: 6px 10px;
    background: var(--gray-25);
    font-size: 12px;
    color: var(--gray-700);
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    margin-bottom: 6px;
  }

  .kb-results {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .file-group-item {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    padding: 6px 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
    transition: all 0.15s ease;

    &:hover {
      background: var(--gray-25);
      border-color: var(--gray-200);
    }

    .file-info {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1;
      min-width: 0;
      padding: 0;
      border: 0;
      background: transparent;
      text-align: left;
      cursor: pointer;

      &:focus-visible {
        outline: 2px solid var(--main-400);
        outline-offset: 2px;
        border-radius: 4px;
      }

      .file-icon {
        flex-shrink: 0;
        color: var(--gray-600);
      }

      .file-name {
        font-size: 13px;
        color: var(--gray-800);
        font-weight: 500;
        flex: 1;
        min-width: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .chunk-count {
        font-size: 11px;
        color: var(--gray-600);
        background: var(--gray-50);
        border: 1px solid var(--gray-150);
        padding: 1px 6px;
        border-radius: 10px;
        white-space: nowrap;
      }
    }

    .file-actions {
      display: flex;
      align-items: center;
      margin-left: 8px;

      .view-file-btn {
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 24px;
        height: 24px;
        border: none;
        background: transparent;
        border-radius: 4px;
        cursor: pointer;
        color: var(--gray-500);
        transition: all 0.15s;

        &:hover {
          background: var(--gray-100);
          color: var(--gray-700);
        }
      }
    }
  }

  .no-results {
    text-align: center;
    color: var(--gray-700);
    padding: 10px;
    font-size: 12px;
    border: 1px dashed var(--gray-200);
    border-radius: 8px;
  }
}
</style>
