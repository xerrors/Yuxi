import { computed, onScopeDispose, ref, watch } from 'vue'
import { agentApi } from '@/apis'

/** 一次读取完整协作摘要，状态查询不读取 checkpoint。 */
export function useSessionCooperation({ threadId, enabled, onSnapshot, loadSummary = (id) => agentApi.getCooperationSummary(id) }) {
  const tree = ref(null)
  const error = ref('')
  const loading = ref(false)
  let generation = 0
  const refresh = async () => {
    if (!threadId.value || !enabled.value || loading.value) return
    const current = generation
    const id = threadId.value
    loading.value = true
    try {
      const response = await loadSummary(id)
      if (current !== generation) return
      tree.value = response
      error.value = ''
      void onSnapshot?.(response.sessions || [])
    } catch (failure) {
      if (current === generation) error.value = failure.message || '协作状态读取失败'
    } finally {
      if (current === generation) loading.value = false
    }
  }
  watch(
    [threadId, enabled],
    ([id], previous) => {
      generation += 1
      loading.value = false
      if (id !== previous?.[0]) {
        tree.value = null
        error.value = ''
      }
      void refresh()
    },
    { immediate: true }
  )
  const timer = setInterval(() => {
    void refresh()
  }, 3000)
  onScopeDispose(() => {
    generation += 1
    clearInterval(timer)
  })
  return {
    sessions: computed(() => tree.value?.sessions || []),
    error,
    refresh
  }
}
