export function newScheduledAgentRequestId() {
  return (
    globalThis.crypto?.randomUUID?.() ||
    `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  )
}

export function createRetriableRequestIds(createRequestId = newScheduledAgentRequestId) {
  const requestIds = new Map()
  return {
    get(key) {
      const requestId = requestIds.get(key) || createRequestId()
      requestIds.set(key, requestId)
      return requestId
    },
    complete(key) {
      requestIds.delete(key)
    }
  }
}

function isUnknownCreateResult(error) {
  if (error?.status == null) return true
  const status = Number(error.status)
  return !Number.isInteger(status) || status === 408 || status >= 500
}

export function createScheduledAgentAutosave({
  persist,
  onPersisted,
  onState,
  delay = 600,
  createRequestId = newScheduledAgentRequestId
}) {
  let timer = null
  let latest = null
  let drain = null
  let draftToken = 0
  let draftJobId = null
  let draftRequestId = null
  let pendingCreate = null
  let revision = 0
  let state = 'idle'

  function publish(nextState, error = '') {
    state = nextState
    onState({ state: nextState, saving: nextState === 'saving', error })
  }

  function clearTimer() {
    if (timer === null) return
    clearTimeout(timer)
    timer = null
  }

  async function save(saveRequest) {
    publish('saving')
    const creating = !saveRequest.jobId
    if (creating && !pendingCreate) pendingCreate = saveRequest
    try {
      const savedJob = await persist(saveRequest)
      let finalizeDraft = false
      if (creating) {
        pendingCreate = null
        draftJobId = savedJob.id
        if (latest?.draftToken === saveRequest.draftToken && !latest.jobId) {
          latest.jobId = savedJob.id
          latest.requestId = null
        }
      }
      if (
        saveRequest.draftToken === draftToken &&
        saveRequest.revision === revision &&
        (!draftJobId || savedJob.id === draftJobId)
      ) {
        finalizeDraft = Boolean(draftJobId)
      }
      onPersisted(savedJob, {
        created: creating,
        finalizeDraft
      })
      if (finalizeDraft) draftJobId = null
      if (saveRequest.revision === revision) publish('saved')
      return true
    } catch (error) {
      const unknownCreateResult = creating && isUnknownCreateResult(error)
      if (creating && !unknownCreateResult) {
        pendingCreate = null
        draftRequestId = createRequestId()
        if (latest && !latest.jobId) latest.requestId = draftRequestId
      }
      if (saveRequest.revision === revision) {
        if ((!creating || !unknownCreateResult) && !latest) {
          if (creating) saveRequest.requestId = draftRequestId
          latest = saveRequest
        }
        publish('error', `${error.message || '自动保存失败'}，请重试后再离开`)
      } else if (unknownCreateResult && state !== 'invalid') {
        publish('error', `${error.message || '自动保存失败'}，请重试后再离开`)
      }
      return false
    }
  }

  function canLeave() {
    return state !== 'error' && state !== 'invalid'
  }

  async function drainSaves() {
    while (true) {
      clearTimer()
      if (state === 'invalid' && !latest) return false
      if (!pendingCreate && !latest) return canLeave()

      const saveRequest = pendingCreate || latest
      if (saveRequest === latest) latest = null
      const saved = await save(saveRequest)
      if (!saved && (pendingCreate || !latest || state === 'error')) return false
    }
  }

  function flush() {
    clearTimer()
    if (drain) return drain
    const currentDrain = drainSaves()
    drain = currentDrain
    const releaseDrain = () => {
      if (drain === currentDrain) drain = null
    }
    currentDrain.then(releaseDrain, releaseDrain)
    return currentDrain
  }

  function queue({ payload, error }, selectedJobId) {
    clearTimer()
    revision += 1
    if (error || !payload) {
      latest = null
      publish('invalid')
      return
    }
    latest = {
      payload,
      jobId: selectedJobId || draftJobId,
      requestId:
        selectedJobId || draftJobId ? null : draftRequestId || (draftRequestId = createRequestId()),
      draftToken,
      revision
    }
    publish('dirty')
    timer = setTimeout(() => void flush(), delay)
  }

  function beginDraft() {
    draftToken += 1
    draftJobId = null
    draftRequestId = createRequestId()
    pendingCreate = null
    publish('idle')
  }

  function leaveEditor() {
    clearTimer()
    latest = null
    draftJobId = null
    draftRequestId = null
    pendingCreate = null
    publish('idle')
  }

  function canDiscardInvalidDraft() {
    return state === 'invalid' && !pendingCreate
  }

  return { beginDraft, canDiscardInvalidDraft, flush, leaveEditor, queue }
}
