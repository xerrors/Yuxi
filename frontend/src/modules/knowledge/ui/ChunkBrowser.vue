<template>
  <div v-if="loading" class="loading-container"><a-spin tip="正在加载分块内容..." /></div>
  <div v-else-if="error" class="chunk-empty" role="alert">{{ error }}</div>
  <div v-else-if="!chunks.length" class="chunk-empty">暂无分块信息，请先完成文件入库。</div>
  <div v-else class="chunk-browser">
    <aside class="chunk-sidebar" aria-label="分块列表">
      <div class="chunk-search">
        <label :for="searchId">搜索分块</label>
        <a-input :id="searchId" v-model:value="query" placeholder="搜索文本或 chunk_id" allow-clear>
          <template #prefix><Search :size="14" aria-hidden="true" /></template>
        </a-input>
        <span class="chunk-count" role="status"
          >{{ filteredChunks.length }} / {{ chunks.length }} 个片段</span
        >
      </div>
      <div class="chunk-list">
        <button
          v-for="chunk in filteredChunks"
          :key="chunk.id"
          type="button"
          class="chunk-list-item"
          :class="{ selected: selectedChunk?.id === chunk.id }"
          :aria-pressed="selectedChunk?.id === chunk.id"
          @click="selectedId = chunk.id"
        >
          <span class="chunk-list-title"
            >#{{ chunk.chunk_order_index }} <code>{{ chunk.id }}</code></span
          >
          <span class="chunk-excerpt">{{ chunk.content }}</span>
        </button>
        <div v-if="!filteredChunks.length" class="chunk-empty">
          没有匹配的分块，请尝试其他文本或 chunk_id。
        </div>
      </div>
    </aside>
    <section v-if="selectedChunk" class="chunk-preview" aria-label="分块完整内容">
      <header class="chunk-preview-header">
        <code class="chunk-id">{{ selectedChunk.id }}</code>
        <a-button v-if="editable" type="link" size="small" @click="$emit('edit', selectedChunk)">
          <template #icon><Pencil :size="14" aria-hidden="true" /></template>
          编辑文件
        </a-button>
      </header>
      <div :key="selectedChunk.id" class="chunk-preview-body">
        <MarkdownDocumentPreview
          v-if="selectedChunk.content"
          :content="selectedChunk.content"
          source
        />
        <div v-else class="chunk-empty">此分块内容为空。</div>
      </div>
    </section>
    <div v-else class="chunk-empty chunk-preview">没有匹配的分块，清空搜索后可继续查看。</div>
  </div>
</template>

<script setup>
import { computed, ref, useId } from 'vue'
import { Pencil, Search } from '@lucide/vue'
import MarkdownDocumentPreview from '@/modules/workspace/ui/MarkdownDocumentPreview.vue'

const props = defineProps({
  chunks: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  editable: { type: Boolean, default: false }
})
defineEmits(['edit'])
const searchId = useId()
const query = ref('')
const selectedId = ref(null)
const filteredChunks = computed(() => {
  const term = query.value.trim().toLowerCase()
  return [...props.chunks]
    .sort((a, b) => a.chunk_order_index - b.chunk_order_index)
    .filter(
      (chunk) =>
        !term || chunk.id.toLowerCase().includes(term) || chunk.content.toLowerCase().includes(term)
    )
})
const selectedChunk = computed(
  () =>
    filteredChunks.value.find((chunk) => chunk.id === selectedId.value) || filteredChunks.value[0]
)
</script>

<style scoped lang="less">
.chunk-browser {
  display: flex;
  height: 100%;
  min-height: 0;
}
.chunk-sidebar {
  display: flex;
  flex-direction: column;
  width: 30%;
  min-width: 220px;
  max-width: 340px;
  border-right: 1px solid var(--gray-150);
}
.chunk-search {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-bottom: 1px solid var(--gray-150);
}
.chunk-search label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text);
}
.chunk-count {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.chunk-list {
  overflow-y: auto;
  min-height: 0;
}
.chunk-list-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  padding: 12px 16px;
  text-align: left;
  color: var(--color-text);
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--gray-150);
  cursor: pointer;
}
.chunk-list-item:hover {
  background: var(--gray-50);
}
.chunk-list-item.selected {
  background: var(--main-10);
  box-shadow: inset 3px 0 var(--main-color);
}
.chunk-list-item:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}
.chunk-list-title {
  display: flex;
  gap: 8px;
  width: 100%;
  font-size: 12px;
}
.chunk-list-title code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chunk-excerpt {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.chunk-preview {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.chunk-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--gray-150);
}
.chunk-id {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
  color: var(--color-text);
}
.chunk-preview-header :deep(.ant-btn) {
  flex-shrink: 0;
}
.chunk-preview-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.chunk-empty {
  padding: 32px 16px;
  text-align: center;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}
</style>
