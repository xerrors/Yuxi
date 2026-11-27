import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionRuntimeStore } from './sessionRuntime'
import { MessageProcessor } from './messageProcessor'
import { getMessageRunId } from './messageDebug'
import { groupRunContinuations } from './runProcessGrouping'

/** 从共享消息事实派生阅读分组。 */
export function useThreadMessageProjection(threadId) {
  const runtime = useSessionRuntimeStore()
  const { threadMessages, threadRuns } = storeToRefs(runtime)
  const { getThreadState } = runtime
  const currentThreadState = computed(() => getThreadState(threadId.value))
  const currentThreadMessages = computed(() => threadMessages.value[threadId.value] || [])
  const currentThreadRuns = computed(() => threadRuns.value[threadId.value] || [])
  const runGroups = computed(() => {
    const state = currentThreadState.value
    const messages = currentThreadMessages.value.map((item) => ({
      ...item,
      content: state?.displayText[item.id] ?? item.content
    }))
    const inputIds = new Set(
      messages.filter((item) => item.type === 'human').map((item) => item.input_id)
    )
    const optimistic = Object.values(state?.optimisticInputs || {})
      .flat()
      .filter((item) => !inputIds.has(item.extra_metadata?.input_id))
    const groups = MessageProcessor.convertServerHistoryToMessages(
      [...messages, ...optimistic],
      currentThreadRuns.value
    )
    for (const group of groups) {
      if (
        state?.isStreaming &&
        (group.run?.id === state.activeRunId ||
          group.messages.some(
            (item) =>
              getMessageRunId(item) === state.activeRunId || item.id === state.pendingInputId
          ))
      ) {
        group.status = 'streaming'
      }
    }
    return groupRunContinuations(groups)
  })

  return { runGroups, currentThreadRuns }
}
