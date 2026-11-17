import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { getSessionMentionPlugins } from '@/apis/mention_api'

/** 已有会话按后端快照生成插件候选；草稿沿用智能体选择。 */
export function useSessionPluginMentions(threadId, active) {
  const plugins = ref(null)

  watch([threadId, active], async ([id, visible], _previous, onCleanup) => {
    plugins.value = id ? [] : null
    if (!id || !visible) return

    const controller = new AbortController()
    onCleanup(() => controller.abort())
    try {
      const result = await getSessionMentionPlugins(id, controller.signal)
      if (!controller.signal.aborted) plugins.value = result
    } catch {
      if (!controller.signal.aborted) {
        message.error('插件列表加载失败，请重新打开对话重试')
      }
    }
  }, { immediate: true, flush: 'sync' })

  return plugins
}
