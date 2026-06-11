<template>
  <a-modal
    :open="open"
    :footer="null"
    :width="800"
    :destroyOnClose="true"
    @cancel="$emit('update:open', false)"
  >
    <template #title>
      <div class="subagent-thread-modal-title">
        <FallbackAvatar
          class="subagent-thread-modal-avatar"
          :src="subagentAvatar"
          :default-src="subagentDefaultAvatar"
          :name="modalTitleName"
          :seed="childThreadId || modalTitleName"
          kind="agent"
          :size="28"
          shape="rounded"
          :alt="`${modalTitleName} icon`"
        />
        <span class="subagent-thread-modal-name">{{ modalTitleName }}</span>
      </div>
    </template>
    <div class="subagent-thread-modal-body">
      <div v-if="loading" class="subagent-thread-modal-state">正在加载子智能体消息...</div>
      <div v-else-if="error" class="subagent-thread-modal-state is-error">{{ error }}</div>
      <ThreadMessageList v-else :messages="messages" />
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { agentApi } from '@/apis'
import { MessageProcessor } from '@/utils/messageProcessor'
import ThreadMessageList from '@/components/ThreadMessageList.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  },
  childThreadId: {
    type: String,
    default: ''
  },
  subagentName: {
    type: String,
    default: ''
  },
  subagentAvatar: {
    type: String,
    default: ''
  },
  subagentDefaultAvatar: {
    type: String,
    default: ''
  }
})

defineEmits(['update:open'])

const loading = ref(false)
const error = ref('')
const messages = ref([])

const modalTitleName = computed(() => props.subagentName || '子智能体')

// LangChain 内容块数组 → 纯文本（仅保留 text 块）
const flattenContent = (content) => {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .filter((block) => block && block.type === 'text')
      .map((block) => block.text || '')
      .join('')
  }
  return content ?? ''
}

const loadHistory = async (threadId) => {
  if (!threadId) return
  loading.value = true
  error.value = ''
  messages.value = []
  try {
    // 子智能体消息存于 LangGraph checkpoint，需走 state 接口并把 tool 结果嵌入 AI 消息。
    const response = await agentApi.getAgentState(threadId, { includeMessages: true })
    // checkpoint 的 content 可能是 LangChain 内容块数组，扁平成文本供 MarkdownPreview 渲染。
    const normalized = (response.messages || []).map((msg) => ({
      ...msg,
      content: flattenContent(msg.content)
    }))
    messages.value = MessageProcessor.convertToolResultToMessages(normalized)
  } catch (e) {
    error.value = '加载子智能体消息失败'
    console.error('Failed to load subagent thread messages:', e)
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.open, props.childThreadId],
  ([isOpen, threadId]) => {
    if (isOpen && threadId) loadHistory(threadId)
  },
  { immediate: true }
)
</script>

<style lang="less" scoped>
.subagent-thread-modal-title {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding-right: 24px;
}

.subagent-thread-modal-avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-0);
  object-fit: cover;
}

.subagent-thread-modal-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-thread-modal-body {
  max-height: 70vh;
  overflow-y: auto;
}

.subagent-thread-modal-state {
  padding: 32px 0;
  text-align: center;
  color: var(--gray-500);
  font-size: 13px;

  &.is-error {
    color: var(--color-error-600);
  }
}
</style>
