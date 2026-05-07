<template>
  <a-modal
    v-model:open="visible"
    :title="modalTitle"
    width="960px"
    :footer="null"
    :destroyOnClose="true"
    wrap-class-name="chunk-detail-modal"
    :bodyStyle="{ maxHeight: '72vh', overflowY: 'auto', padding: '0' }"
  >
    <div v-if="chunk" class="detail-meta">
      <span v-if="typeof chunk.score === 'number'" class="score"
        >相似度 {{ (chunk.score * 100).toFixed(1) }}%</span
      >
      <span v-if="chunk.metadata?.chunk_id" class="meta-item"
        >chunk_id: {{ chunk.metadata.chunk_id }}</span
      >
    </div>

    <MarkdownPreview v-if="chunk?.content" :content="chunk.content" class="chunk-markdown-content" />
    <div v-else class="empty-text">暂无内容</div>
  </a-modal>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  },
  chunk: {
    type: Object,
    default: null
  },
  titlePrefix: {
    type: String,
    default: '文档片段详情'
  }
})

const emit = defineEmits(['update:open'])

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const modalTitle = computed(() => {
  const source = props.chunk?.metadata?.source
  return source ? `${props.titlePrefix} - ${source}` : props.titlePrefix
})
</script>

<style scoped lang="less">
.detail-meta {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--gray-600);
  display: flex;
  gap: 10px;

  .score {
    color: var(--gray-700);
    font-weight: 600;
  }

  .meta-item {
    color: var(--gray-600);
  }
}

.empty-text {
  color: var(--gray-500);
  font-size: 13px;
}
</style>
