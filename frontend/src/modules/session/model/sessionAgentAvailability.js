import { ref, watch } from 'vue'

const isWorkdirUnavailable = (error) =>
  error?.response?.data?.detail?.code === 'session_workdir_unavailable'

/** 详情可执行性独立于历史读取，并随对话和身份失效。 */
export function useSessionAgentAvailability(currentThreadId, authToken, loadAgent, checkWorkdir) {
  const status = ref('loading')
  let requestVersion = 0

  watch([currentThreadId, authToken], () => {
    requestVersion += 1
    status.value = 'loading'
  }, { flush: 'sync' })

  async function refresh(threadId, agentId) {
    if (currentThreadId.value !== threadId) return
    const version = ++requestVersion
    status.value = 'loading'
    const [agent, workdir] = await Promise.allSettled([loadAgent(agentId), checkWorkdir(threadId)])
    let next = 'ready'
    if (workdir.status === 'rejected' && isWorkdirUnavailable(workdir.reason)) {
      next = 'workdir-unavailable'
    } else if (agent.status === 'rejected') {
      next = [403, 404].includes(agent.reason.status) ? 'unavailable' : 'error'
    } else if (!agent.value || agent.value.can_run === false) {
      next = 'unavailable'
    } else if (workdir.status === 'rejected') {
      next = 'error'
    }
    if (version === requestVersion) status.value = next
  }

  function handleExecutionError(threadId, error) {
    if (threadId === currentThreadId.value && isWorkdirUnavailable(error)) {
      requestVersion += 1
      status.value = 'workdir-unavailable'
    }
  }

  return { status, refresh, handleExecutionError }
}
