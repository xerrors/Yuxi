import { agentApi } from '@/apis'
import { processRunSseResponse } from '@/composables/useAgentRunStream'
import { IDLE_QUEUE_SNAPSHOT } from '@/composables/useAgentThreadState'
import { handleChatError } from '@/utils/errorHandler'
import { applySteerRequestEvent } from '@/utils/agentRequestQueue'

export function useAgentRequestQueue({
  getThreadState,
  startRunStream,
  onStreamError,
  onSteerDispatched
}) {
  const removeRequestFromQueue = (ts, requestId) => {
    if (!ts || !ts.queuedRequests) return
    ts.queuedRequests = ts.queuedRequests.filter((r) => r.request_id !== requestId)
  }

  const stopRequestStream = (threadId, requestId) => {
    const ts = getThreadState(threadId)
    const entry = ts?.requestStreams?.[requestId]
    if (!entry) return
    entry.controller?.abort()
    delete ts.requestStreams[requestId]
  }

  const stopAllRequestStreams = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts?.requestStreams) return
    for (const rid of Object.keys(ts.requestStreams)) {
      stopRequestStream(threadId, rid)
    }
  }

  const cancelRequest = async (threadId, requestId) => {
    const ts = getThreadState(threadId)
    if (!ts || !requestId) return false
    try {
      await agentApi.cancelRequest(requestId)
      stopRequestStream(threadId, requestId)
      removeRequestFromQueue(ts, requestId)
      if (ts.onGoingConv?.msgChunks) {
        delete ts.onGoingConv.msgChunks[requestId]
      }
      return true
    } catch (error) {
      if (error?.name !== 'AbortError') {
        handleChatError(error, 'cancel')
      }
      return false
    }
  }

  const syncQueuedRequests = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    try {
      const resp = await agentApi.listThreadQueuedRequests(threadId, agentSlug)
      ts.queuedRequests = resp?.requests || []
      ts.queueSnapshot = resp?.queue || { ...IDLE_QUEUE_SNAPSHOT }
    } catch (e) {
      console.warn('Failed to sync queued requests:', e)
    }
  }

  const startRequestStream = async (threadId, requestId) => {
    if (!threadId || !requestId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    ts.requestStreams = ts.requestStreams || {}
    if (ts.requestStreams[requestId]) return

    const controller = new AbortController()
    const queuedRequest = ts.queuedRequests?.find((request) => request.request_id === requestId)
    const entry = {
      controller,
      position: 0,
      status: 'queued',
      queuePolicy: queuedRequest?.queue_policy
    }
    ts.requestStreams[requestId] = entry

    try {
      const response = await agentApi.streamRequestEvents(requestId, {
        signal: controller.signal
      })
      if (!response.ok) {
        throw new Error(`Request SSE response not ok: ${response.status}`)
      }

      const handleEvent = (event, data) => {
        // 一次性取 ts/entry，避免每个分支重复 getThreadState 触发响应式追踪。
        const tsInner = getThreadState(threadId)
        const innerEntry = tsInner?.requestStreams?.[requestId]
        if (!tsInner || innerEntry?.controller !== controller) return

        if (event === 'queued' && data) {
          entry.position = data.position || entry.position
          const queuedRequest = tsInner.queuedRequests?.find((r) => r.request_id === requestId)
          if (queuedRequest) queuedRequest.queue_position = entry.position
        } else if ((event === 'steering' || event === 'steer_ready') && data) {
          entry.status = event === 'steer_ready' ? 'steer_ready' : 'queued'
          entry.queuePolicy = 'steer'
          applySteerRequestEvent(tsInner.queuedRequests, requestId, event, data)
        } else if (event === 'run_created' && data) {
          entry.status = 'dispatched'
          if (data.run_id) {
            const dispatchedRequest = tsInner.queuedRequests?.find(
              (r) => r.request_id === requestId
            )
            removeRequestFromQueue(tsInner, requestId)
            stopRequestStream(threadId, requestId)
            if (
              (dispatchedRequest?.queue_policy === 'steer' || entry.queuePolicy === 'steer') &&
              typeof onSteerDispatched === 'function'
            ) {
              onSteerDispatched(threadId, requestId, data.run_id)
            }
            void startRunStream(threadId, data.run_id, requestId)
          }
        } else if (event === 'cancelled' || event === 'rejected' || event === 'failed') {
          entry.status = event
          const failedRequest = tsInner.queuedRequests?.find((r) => r.request_id === requestId)
          tsInner.isStreaming = false
          tsInner.replyLoadingVisible = false
          tsInner.pendingRequestId = null
          delete tsInner.onGoingConv.msgChunks[requestId]
          removeRequestFromQueue(tsInner, requestId)
          stopRequestStream(threadId, requestId)
          if (typeof onStreamError === 'function') {
            onStreamError(threadId, requestId, event, {
              errorCode: data?.error_code,
              queuePolicy: failedRequest?.queue_policy,
              content: failedRequest?.content
            })
          }
        }
      }

      await processRunSseResponse(response, handleEvent)
    } catch (error) {
      if (error?.name !== 'AbortError') {
        console.error('Request SSE stream error:', error)
        handleChatError(error, 'stream')
      }
    } finally {
      const tsFinal = getThreadState(threadId)
      if (tsFinal?.requestStreams?.[requestId]?.controller === controller) {
        delete tsFinal.requestStreams[requestId]
      }
    }
  }

  const continueQueue = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || ts.continueQueueInFlight) return false

    ts.continueQueueInFlight = true
    try {
      const response = await agentApi.continueThreadQueue(threadId, agentSlug)
      await syncQueuedRequests(threadId, agentSlug)
      if (response?.request_id) {
        void startRequestStream(threadId, response.request_id)
      }
      return true
    } catch (error) {
      handleChatError(error, 'continue_queue')
      return false
    } finally {
      ts.continueQueueInFlight = false
    }
  }

  const steerRequest = async (threadId, agentSlug, requestId) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || !requestId) return false
    try {
      await agentApi.steerRequest(requestId)
      await syncQueuedRequests(threadId, agentSlug)
      void startRequestStream(threadId, requestId)
      return true
    } catch (error) {
      handleChatError(error, 'steer')
      return false
    }
  }

  return {
    startRequestStream,
    stopAllRequestStreams,
    cancelRequest,
    syncQueuedRequests,
    continueQueue,
    steerRequest
  }
}
