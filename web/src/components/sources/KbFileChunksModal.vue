<template>
  <a-modal
    v-model:open="visible"
    :title="null"
    width="760px"
    :footer="null"
    :closable="false"
    :destroy-on-close="true"
    wrap-class-name="kb-file-chunks-modal"
    :body-style="{ maxHeight: '80vh', overflowY: 'auto', padding: 0 }"
    :aria-labelledby="titleId"
  >
    <header class="modal-header">
      <div class="file-heading">
        <span class="file-icon-box" aria-hidden="true">
          <FileText :size="18" stroke-width="1.8" />
        </span>
        <div class="file-heading-copy">
          <span class="eyebrow">知识库检索</span>
          <h2 :id="titleId" class="file-name" :title="fileGroup?.filename">
            {{ fileGroup?.filename || '检索结果' }}
          </h2>
        </div>
      </div>

      <div class="header-actions">
        <div class="chunk-summary" aria-label="片段数量">
          <strong>{{ chunks.length }}</strong>
          <span>个片段</span>
        </div>
        <button
          type="button"
          class="close-btn"
          aria-label="关闭检索片段弹窗"
          @click="visible = false"
        >
          <X :size="18" stroke-width="1.8" aria-hidden="true" />
        </button>
      </div>
    </header>

    <div class="modal-content">
      <section v-if="chunks.length > 0" class="chunks-container" aria-label="检索片段列表">
        <article
          v-for="(chunk, index) in chunks"
          :key="getChunkKey(chunk, index)"
          class="chunk-item"
        >
          <header class="chunk-header">
            <div class="chunk-heading">
              <span class="chunk-index">{{ formatChunkIndex(index) }}</span>
              <span class="chunk-label">片段</span>
            </div>

            <div
              v-if="hasScore(chunk.score) || hasScore(chunk.rerank_score) || getLineRange(chunk)"
              class="chunk-meta"
            >
              <span v-if="hasScore(chunk.score)" class="metric">
                <span class="metric-label">相似度</span>
                <strong class="metric-value">{{ formatScore(chunk.score) }}</strong>
              </span>
              <span v-if="hasScore(chunk.rerank_score)" class="metric">
                <span class="metric-label">重排</span>
                <strong class="metric-value">{{ formatScore(chunk.rerank_score) }}</strong>
              </span>
              <span v-if="getLineRange(chunk)" class="chunk-location">{{
                getLineRange(chunk)
              }}</span>
            </div>
          </header>

          <div class="chunk-body">
            <MarkdownPreview
              v-if="chunk.content"
              :content="chunk.content"
              :compact="true"
              class="chunk-markdown"
            />
            <div v-else class="empty-text">暂无内容</div>
          </div>
        </article>
      </section>

      <div v-else class="empty-state" role="status">
        <FileText :size="20" stroke-width="1.6" aria-hidden="true" />
        <span>暂无可展示的文档片段</span>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, useId } from 'vue'
import { FileText, X } from '@lucide/vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  },
  fileGroup: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:open'])
const titleId = useId()

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const chunks = computed(() => {
  return Array.isArray(props.fileGroup?.chunks) ? props.fileGroup.chunks : []
})

const getChunkKey = (chunk, index) => {
  if (chunk?.metadata?.chunk_id) return `${chunk.metadata.chunk_id}-${index}`
  if (chunk?.id) return `${chunk.id}-${index}`
  return `chunk-${index}`
}

const hasScore = (value) => typeof value === 'number' && Number.isFinite(value)

const formatScore = (value) => `${(value * 100).toFixed(1)}%`

const formatChunkIndex = (index) => String(index + 1).padStart(2, '0')

const getLineRange = (chunk) => {
  const startLine = Number(chunk?.metadata?.start_line || 0)
  const endLine = Number(chunk?.metadata?.end_line || 0)
  if (!startLine || !endLine) return ''
  return startLine === endLine ? `第 ${startLine} 行` : `第 ${startLine}-${endLine} 行`
}
</script>

<style scoped lang="less">
.modal-header {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 24px 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);

  .file-heading {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }

  .file-icon-box {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    flex-shrink: 0;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-25);
    color: var(--main-700);
  }

  .file-heading-copy {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .eyebrow {
    color: var(--gray-500);
    font-size: 11px;
    line-height: 16px;
    letter-spacing: 0.04em;
  }

  .file-name {
    margin: 0;
    overflow: hidden;
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 600;
    line-height: 22px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }

  .chunk-summary {
    display: flex;
    align-items: baseline;
    gap: 4px;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 18px;
    white-space: nowrap;

    strong {
      color: var(--gray-900);
      font-size: 18px;
      font-variant-numeric: tabular-nums;
      font-weight: 600;
      line-height: 20px;
    }
  }

  .close-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border: 1px solid transparent;
    border-radius: 6px;
    background: transparent;
    color: var(--gray-500);
    cursor: pointer;
    transition:
      background-color 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease;

    &:hover {
      border-color: var(--gray-150);
      background: var(--gray-50);
      color: var(--gray-800);
    }

    &:active {
      background: var(--gray-100);
    }

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 2px;
    }
  }
}

.modal-content {
  padding: 16px 24px 24px;
}

.chunks-container {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
}

.chunk-item {
  &:not(:last-child) {
    border-bottom: 1px solid var(--gray-150);
  }

  .chunk-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    min-height: 42px;
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--gray-100);
    background: var(--gray-10);
  }

  .chunk-heading {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }

  .chunk-index {
    color: var(--main-700);
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    line-height: 18px;
  }

  .chunk-label {
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 600;
    line-height: 18px;
  }

  .chunk-meta {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 4px 12px;
    min-width: 0;
  }

  .metric {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    color: var(--gray-500);
    font-size: 11px;
    line-height: 18px;
    white-space: nowrap;
  }

  .metric-label {
    color: var(--gray-500);
  }

  .metric-value {
    color: var(--main-700);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }

  .chunk-location {
    padding-left: 12px;
    border-left: 1px solid var(--gray-200);
    color: var(--gray-500);
    font-size: 11px;
    line-height: 18px;
    white-space: nowrap;
  }

  .chunk-body {
    padding: 14px 16px 18px;
    background: var(--gray-0);
    color: var(--gray-800);

    .chunk-markdown {
      :deep(p) {
        margin: 0 0 8px;
      }

      :deep(p:last-child) {
        margin-bottom: 0;
      }

      :deep(h1),
      :deep(h2),
      :deep(h3),
      :deep(h4),
      :deep(h5),
      :deep(h6) {
        margin: 12px 0 6px;
        color: var(--gray-900);
        line-height: 1.4;
      }

      :deep(h1:first-child),
      :deep(h2:first-child),
      :deep(h3:first-child),
      :deep(h4:first-child),
      :deep(h5:first-child),
      :deep(h6:first-child) {
        margin-top: 0;
      }

      :deep(ul),
      :deep(ol) {
        margin: 6px 0;
      }

      :deep(li) {
        margin: 2px 0;
      }

      :deep(blockquote) {
        margin: 8px 0;
      }

      :deep(hr),
      :deep(pre.shiki),
      :deep(.markdown-code-block),
      :deep(table),
      :deep(.html-preview-render),
      :deep(.svg-inline-render) {
        margin-top: 10px;
        margin-bottom: 10px;
      }
    }

    .empty-text {
      color: var(--gray-500);
      font-size: 13px;
    }
  }
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 160px;
  border: 1px dashed var(--gray-200);
  border-radius: 10px;
  color: var(--gray-500);
  font-size: 13px;
}

@media (max-width: 640px) {
  .modal-header {
    align-items: flex-start;
    padding: 14px 16px 13px;

    .file-heading {
      gap: 9px;
    }

    .file-icon-box {
      width: 32px;
      height: 32px;
      border-radius: 7px;
    }

    .file-name {
      font-size: 14px;
      line-height: 20px;
    }

    .header-actions {
      gap: 6px;
    }

    .chunk-summary {
      font-size: 11px;

      strong {
        font-size: 16px;
      }
    }
  }

  .modal-content {
    padding: 12px 16px 16px;
  }

  .chunk-item {
    .chunk-header {
      align-items: flex-start;
      flex-direction: column;
      gap: 4px;
      padding: 9px 12px 8px;
    }

    .chunk-meta {
      justify-content: flex-start;
      gap: 4px 10px;
    }

    .chunk-location {
      padding-left: 0;
      border-left: 0;
    }

    .chunk-body {
      padding: 12px 12px 16px;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .close-btn {
    transition: none;
  }
}
</style>

<style lang="less">
.kb-file-chunks-modal {
  .ant-modal {
    max-width: calc(100vw - 24px);
    padding-bottom: 0;
  }

  .ant-modal .ant-modal-content {
    padding: 0;
    overflow: hidden;
    border: 1px solid var(--gray-150);
    border-radius: 10px;
    background: var(--gray-0);
    box-shadow: 0 18px 48px var(--shadow-3);
  }

  .ant-modal-body {
    padding: 0;
  }

  @media (max-width: 640px) {
    .ant-modal {
      top: 16px;
      margin: 0 auto;
    }
  }
}
</style>
