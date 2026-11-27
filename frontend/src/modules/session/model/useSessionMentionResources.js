import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { getSessionMentionResources } from '@/apis/mention_api'

/** 已有会话读取快照候选，草稿交给智能体目录。 */
export function useSessionMentionResources(threadId, active, authToken) {
  const resources = ref(null)

  watch([threadId, active, authToken], async ([id, visible, token], _previous, onCleanup) => {
    resources.value = id ? {} : null
    if (!id || !visible || !token) return

    const controller = new AbortController()
    onCleanup(() => controller.abort())
    try {
      const result = await getSessionMentionResources(id, controller.signal)
      if (!controller.signal.aborted) resources.value = result
    } catch {
      if (!controller.signal.aborted) {
        message.error('会话资源列表加载失败，请重新打开对话重试')
      }
    }
  }, { immediate: true, flush: 'sync' })

  return resources
}
