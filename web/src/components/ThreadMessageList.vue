<template>
  <div class="thread-message-list">
    <template v-for="(conv, convIndex) in conversations" :key="`conv-${convIndex}`">
      <template
        v-for="(displayItem, itemIndex) in displayItemsList[convIndex]"
        :key="displayItem.key"
      >
        <AgentMessageComponent
          v-if="displayItem.type === 'message'"
          :message="displayItem.message"
          :is-processing="isDisplayMessageProcessing(conv, displayItem)"
          :show-refs="false"
          :hide-tool-calls="true"
          :mention="{}"
        />
        <ToolCallsGroupComponent
          v-else
          :tool-calls="displayItem.toolCalls"
          :entries="displayItem.entries"
          :is-active="isToolGroupActive(conv, itemIndex, displayItemsList[convIndex])"
        />
      </template>
      <div v-if="!conv.messages.length && conv.run" class="thread-message-list-empty">
        {{ formatEmptyRunStatus(conv.run.status) }}
      </div>
    </template>
    <div v-if="conversations.length === 0" class="thread-message-list-empty">暂无消息</div>
  </div>
</template>

<script setup>
import { formatEmptyRunStatus } from '@/utils/conversationProcessGrouping'
import { computed } from 'vue'
import AgentMessageComponent from '@/components/AgentMessageComponent.vue'
import ToolCallsGroupComponent from '@/components/ToolCallsGroupComponent.vue'
import { MessageProcessor } from '@/utils/messageProcessor'
import { getConversationDisplayItems } from '@/utils/messageGrouping'

const props = defineProps({
  runs: { type: Array, default: () => [] },
  messages: {
    type: Array,
    default: () => []
  },
  ongoingMessages: {
    type: Array,
    default: () => []
  },
  isProcessing: {
    type: Boolean,
    default: false
  },
  enrichToolCalls: {
    type: Function,
    default: null
  }
})

const historyConversations = computed(() =>
  MessageProcessor.convertServerHistoryToMessages(props.messages, props.runs)
)

const conversations = computed(() => {
  if (!props.ongoingMessages.length) return historyConversations.value
  const liveRunId = props.ongoingMessages.find((message) => message.run_id)?.run_id
  const liveGroup = { messages: props.ongoingMessages, status: 'streaming' }
  if (liveRunId && historyConversations.value.some((conv) => conv.run?.run_id === liveRunId)) {
    return historyConversations.value.map((conv) =>
      conv.run?.run_id === liveRunId ? { ...conv, ...liveGroup } : conv
    )
  }
  return [...historyConversations.value, liveGroup]
})

const displayItemsList = computed(() =>
  conversations.value.map((conv) =>
    getConversationDisplayItems(
      conv,
      props.enrichToolCalls ? { enrichToolCalls: props.enrichToolCalls } : {}
    )
  )
)

const isDisplayMessageProcessing = (conv, displayItem) =>
  Boolean(
    props.isProcessing &&
    displayItem?.type === 'message' &&
    conv?.status === 'streaming' &&
    displayItem.sourceIndex === conv.messages.length - 1
  )

const isToolGroupActive = (conv, itemIndex, displayItems) =>
  Boolean(
    props.isProcessing && conv?.status === 'streaming' && itemIndex === displayItems.length - 1
  )
</script>

<style lang="less" scoped>
.thread-message-list {
  display: flex;
  flex-direction: column;
}

.thread-message-list-empty {
  padding: 24px 0;
  text-align: center;
  color: var(--gray-500);
  font-size: 13px;
}
</style>
