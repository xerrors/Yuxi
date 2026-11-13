<template>
  <div class="turn-sources-panel">
    <div v-if="entryLoading" class="sources-state">正在加载来源...</div>
    <div v-else-if="entryError" class="sources-state sources-error" role="alert">
      <span>{{ entryError }}</span>
      <button type="button" @click="loadEntry">重试</button>
    </div>
    <div v-else-if="!knowledgeChunks.length && !webSources.length" class="sources-state">
      当前回答没有可展示的来源
    </div>
    <template v-else>
      <KnowledgeSourceSection v-if="knowledgeChunks.length" :chunks="knowledgeChunks" />
      <WebSearchSourceSection v-if="webSources.length" :sources="webSources" />
    </template>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import KnowledgeSourceSection from '@/modules/agents/ui/KnowledgeSourceSection.vue'
import WebSearchSourceSection from '@/modules/agents/ui/WebSearchSourceSection.vue'
import { useTurnReferencesStore } from '@/modules/session/model/turnReferences'

const props = defineProps({
  threadId: { type: String, default: '' },
  turnId: { type: String, default: '' },
  // 标注接口未加载或不可用时的回退数据，来自消息内工具调用的提取结果。
  sources: { type: Object, default: () => ({ knowledgeChunks: [], webSources: [] }) },
  active: { type: Boolean, default: false }
})

const referenceStore = useTurnReferencesStore()

const entry = computed(() =>
  props.threadId && props.turnId ? referenceStore.getEntry(props.threadId, props.turnId) : null
)
const entryLoading = computed(() => Boolean(entry.value?.loading))
const entryError = computed(() =>
  entry.value?.loaded && !entry.value?.available ? '' : entry.value?.error || ''
)

/** 只补拉缺失的标注来源；已加载或没有 Turn 归属时保持回退数据。 */
const loadEntry = () => {
  if (!props.threadId || !props.turnId) return
  if (entry.value?.loaded || entry.value?.loading) return
  void referenceStore.load(props.threadId, props.turnId)
}

watch(
  [() => props.threadId, () => props.turnId, () => props.active],
  ([, , active]) => {
    if (active) loadEntry()
  },
  { immediate: true }
)

const knowledgeChunks = computed(() => {
  const source = entry.value
  if (source?.loaded) {
    return source.sources
      .filter((item) => item.kind === 'knowledge')
      .map((item) => ({
        kb_id: item.kb_id,
        file_id: item.file_id,
        supports_document_read: item.supports_document_read,
        supports_documents: item.supports_documents,
        content: item.content,
        metadata: {
          source: item.title,
          chunk_id: item.chunk_id,
          start_line: item.start_line,
          end_line: item.end_line
        }
      }))
  }
  return Array.isArray(props.sources?.knowledgeChunks) ? props.sources.knowledgeChunks : []
})

const webSources = computed(() => {
  const source = entry.value
  if (source?.loaded) return source.sources.filter((item) => item.kind === 'web')
  return Array.isArray(props.sources?.webSources) ? props.sources.webSources : []
})
</script>

<style lang="less" scoped>
.turn-sources-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 10px 12px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.sources-state {
  padding: 24px 8px;
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

.sources-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-error-700);

  button {
    border: 0;
    background: transparent;
    color: var(--main-color);
    cursor: pointer;
  }
}
</style>
