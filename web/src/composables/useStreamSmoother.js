const cloneChunk = (value) => {
  if (typeof structuredClone === 'function') {
    return structuredClone(value)
  }
  return JSON.parse(JSON.stringify(value))
}

const hasText = (value) => typeof value === 'string' && value.length > 0

const createEmptyToolCallChunk = (toolCallChunk) => ({
  ...toolCallChunk,
  args: ''
})

const stripBufferedFields = (chunk) => {
  const stripped = cloneChunk(chunk)
  stripped.content = ''
  stripped.reasoning_content = ''

  if (stripped.additional_kwargs?.reasoning_content !== undefined) {
    stripped.additional_kwargs = {
      ...stripped.additional_kwargs,
      reasoning_content: ''
    }
  }

  if (Array.isArray(stripped.tool_call_chunks)) {
    stripped.tool_call_chunks = stripped.tool_call_chunks.map(createEmptyToolCallChunk)
  }

  return stripped
}

const hasBufferedPayload = (chunk) =>
  hasText(chunk?.content) ||
  hasText(chunk?.reasoning_content) ||
  hasText(chunk?.additional_kwargs?.reasoning_content) ||
  (Array.isArray(chunk?.tool_call_chunks) &&
    chunk.tool_call_chunks.some((item) => hasText(item?.args)))

const appendLoadingChunk = (threadState, chunk) => {
  if (!threadState || !chunk?.id) return
  if (!threadState.onGoingConv.msgChunks[chunk.id]) {
    threadState.onGoingConv.msgChunks[chunk.id] = []
  }
  threadState.onGoingConv.msgChunks[chunk.id].push(chunk)
}

const raf =
  typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'
    ? (callback) => window.requestAnimationFrame(callback)
    : (callback) => setTimeout(() => callback(Date.now()), 16)

const caf =
  typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function'
    ? (id) => window.cancelAnimationFrame(id)
    : (id) => clearTimeout(id)

const DEFAULT_OPTIONS = {
  minChunkSize: 1,
  maxChunkSize: 64,
  defaultIntervalMs: 1000,
  minDrainWindowMs: 400,
  maxDrainWindowMs: 1400,
  targetLagMs: 900,
  minReserveChars: 48,
  maxReserveChars: 240,
  maxBufferedChars: 3000,
  emaAlpha: 0.2,
  basePaceMultiplier: 0.92,
  overflowDivisor: 180,
  maxBurstFactor: 2.6,
  reserveReleaseDelayMs: 1200,
  reserveDecayWindowMs: 2200
}

const getIncomingSize = (chunk) => {
  let total = 0
  total += (chunk?.content || '').length
  total += (chunk?.reasoning_content || '').length
  total += (chunk?.additional_kwargs?.reasoning_content || '').length

  if (Array.isArray(chunk?.tool_call_chunks)) {
    chunk.tool_call_chunks.forEach((item) => {
      total += (item?.args || '').length
    })
  }

  return total
}

const createController = (chunk, options) => ({
  skeleton: stripBufferedFields(chunk),
  contentBuffer: '',
  reasoningBuffer: '',
  additionalReasoningBuffer: '',
  toolCallArgBuffers: new Map(),
  scheduled: false,
  frameId: null,
  lastPushAt: Date.now(),
  lastEmitAt: 0,
  lastFrameAt: Date.now(),
  carryChars: 0,
  avgIntervalMs: options.defaultIntervalMs,
  avgChunkChars: Math.max(options.minReserveChars, getIncomingSize(chunk))
})

const mergeSkeleton = (controller, chunk) => {
  const stripped = stripBufferedFields(chunk)

  Object.entries(stripped).forEach(([key, value]) => {
    if (
      key === 'content' ||
      key === 'reasoning_content' ||
      key === 'tool_call_chunks' ||
      key === 'additional_kwargs'
    ) {
      return
    }
    controller.skeleton[key] = value
  })

  if (stripped.additional_kwargs) {
    controller.skeleton.additional_kwargs = {
      ...(controller.skeleton.additional_kwargs || {}),
      ...stripped.additional_kwargs
    }
  }

  if (Array.isArray(stripped.tool_call_chunks)) {
    const existing = Array.isArray(controller.skeleton.tool_call_chunks)
      ? [...controller.skeleton.tool_call_chunks]
      : []

    stripped.tool_call_chunks.forEach((toolCallChunk) => {
      const index = toolCallChunk?.index
      const existingIndex = existing.findIndex((item) => item?.index === index)
      if (existingIndex >= 0) {
        existing[existingIndex] = {
          ...existing[existingIndex],
          ...toolCallChunk
        }
      } else {
        existing.push(toolCallChunk)
      }
    })

    controller.skeleton.tool_call_chunks = existing
  }
}

const getBufferedLength = (controller) => {
  let total =
    controller.contentBuffer.length +
    controller.reasoningBuffer.length +
    controller.additionalReasoningBuffer.length

  controller.toolCallArgBuffers.forEach((entry) => {
    total += entry.buffer.length
  })

  return total
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

const getReserveSize = (controller, options) => {
  const charsPerMs = controller.avgChunkChars / Math.max(1, controller.avgIntervalMs)
  const lagReserve = Math.ceil(charsPerMs * options.targetLagMs)
  return clamp(
    Math.max(options.minReserveChars, lagReserve),
    options.minReserveChars,
    options.maxReserveChars
  )
}

const getDynamicReserve = (controller, options, now) => {
  const reserveSize = getReserveSize(controller, options)
  const elapsedSincePush = Math.max(0, now - controller.lastPushAt)
  const releaseDelay = Math.max(0, options.reserveReleaseDelayMs)

  if (elapsedSincePush <= releaseDelay) {
    return reserveSize
  }

  const decayProgress = clamp(
    (elapsedSincePush - releaseDelay) / Math.max(1, options.reserveDecayWindowMs),
    0,
    1
  )

  return Math.ceil(reserveSize * (1 - decayProgress))
}

const getBufferedOverflow = (pending, options) => {
  const maxBufferedChars = Number(options.maxBufferedChars)
  if (!Number.isFinite(maxBufferedChars) || maxBufferedChars <= 0) return 0
  return Math.max(0, pending - Math.floor(maxBufferedChars))
}

const getChunkSize = (controller, pending, options) => {
  const now = Date.now()
  const deltaMs = Math.max(16, now - controller.lastFrameAt)
  const charsPerMs = controller.avgChunkChars / Math.max(1, controller.avgIntervalMs)
  const baseRate = charsPerMs * options.basePaceMultiplier
  const dynamicReserve = getDynamicReserve(controller, options, now)
  const overflow = Math.max(0, pending - dynamicReserve)
  const overflowBoost = overflow / options.overflowDivisor
  const maxRate = Math.max(baseRate, charsPerMs * options.maxBurstFactor)
  const pacedRate = clamp(baseRate + overflowBoost, options.minChunkSize / 240, maxRate)

  controller.carryChars += pacedRate * deltaMs
  controller.lastFrameAt = now

  const budget = Math.floor(controller.carryChars)
  if (budget <= 0) return 0

  const maxAllowed = Math.max(1, pending - dynamicReserve)
  const emitCount = Math.min(budget, maxAllowed, options.maxChunkSize)

  if (emitCount <= 0) {
    return 0
  }

  controller.carryChars -= emitCount
  return emitCount
}

const takeFromBuffer = (value, count) => {
  if (!value || count <= 0) {
    return { emitted: '', rest: value || '' }
  }
  return {
    emitted: value.slice(0, count),
    rest: value.slice(count)
  }
}

export function useStreamSmoother({ getThreadState, options = {} }) {
  const resolvedOptions = {
    ...DEFAULT_OPTIONS,
    ...(options || {})
  }

  const controllersByThread = new Map()

  const getThreadControllers = (threadId) => {
    if (!controllersByThread.has(threadId)) {
      controllersByThread.set(threadId, new Map())
    }
    return controllersByThread.get(threadId)
  }

  const emitDelta = (threadId, messageId, forceFlush = false, immediateBudget = null) => {
    const threadState = getThreadState(threadId)
    const threadControllers = controllersByThread.get(threadId)
    const controller = threadControllers?.get(messageId)

    if (!threadState || !controller) return

    const pending = getBufferedLength(controller)
    if (pending <= 0) {
      controller.scheduled = false
      controller.frameId = null
      return
    }

    const hasImmediateBudget = Number.isFinite(immediateBudget) && Math.floor(immediateBudget) > 0
    const budget = forceFlush
      ? pending
      : hasImmediateBudget
        ? Math.min(pending, Math.floor(immediateBudget))
        : getChunkSize(controller, pending, resolvedOptions)
    let remaining = budget
    const delta = stripBufferedFields(controller.skeleton)

    const contentPart = takeFromBuffer(controller.contentBuffer, remaining)
    delta.content = contentPart.emitted
    controller.contentBuffer = contentPart.rest
    remaining -= contentPart.emitted.length

    const reasoningPart = takeFromBuffer(controller.reasoningBuffer, remaining)
    delta.reasoning_content = reasoningPart.emitted
    controller.reasoningBuffer = reasoningPart.rest
    remaining -= reasoningPart.emitted.length

    const additionalReasoningPart = takeFromBuffer(controller.additionalReasoningBuffer, remaining)
    if (
      additionalReasoningPart.emitted ||
      delta.additional_kwargs?.reasoning_content !== undefined
    ) {
      delta.additional_kwargs = {
        ...(delta.additional_kwargs || {}),
        reasoning_content: additionalReasoningPart.emitted
      }
    }
    controller.additionalReasoningBuffer = additionalReasoningPart.rest
    remaining -= additionalReasoningPart.emitted.length

    if (Array.isArray(delta.tool_call_chunks)) {
      delta.tool_call_chunks = delta.tool_call_chunks
        .map((toolCallChunk) => {
          const entry = controller.toolCallArgBuffers.get(toolCallChunk.index)
          if (!entry) return null
          const argPart = takeFromBuffer(
            entry.buffer,
            remaining > 0 ? remaining : forceFlush ? pending : 0
          )
          entry.buffer = argPart.rest
          remaining -= argPart.emitted.length
          return {
            ...toolCallChunk,
            args: argPart.emitted
          }
        })
        .filter(Boolean)
    }

    const hasOutput =
      hasText(delta.content) ||
      hasText(delta.reasoning_content) ||
      hasText(delta.additional_kwargs?.reasoning_content) ||
      (Array.isArray(delta.tool_call_chunks) &&
        delta.tool_call_chunks.some((item) => hasText(item?.args)))

    if (hasOutput) {
      controller.lastEmitAt = Date.now()
      appendLoadingChunk(threadState, delta)
    }

    const remainingPending = getBufferedLength(controller)
    if (remainingPending > 0 && !forceFlush) {
      controller.scheduled = true
      controller.frameId = raf(() => emitDelta(threadId, messageId))
      return
    }

    controller.scheduled = false
    controller.frameId = null
  }

  const schedule = (threadId, messageId) => {
    const controller = controllersByThread.get(threadId)?.get(messageId)
    if (!controller || controller.scheduled) return
    controller.scheduled = true
    controller.frameId = raf(() => emitDelta(threadId, messageId))
  }

  const pushChunk = (chunk, threadId) => {
    const threadState = getThreadState(threadId)
    if (!threadState || !chunk?.id) return

    if (!hasBufferedPayload(chunk)) {
      appendLoadingChunk(threadState, chunk)
      return
    }

    const threadControllers = getThreadControllers(threadId)
    let controller = threadControllers.get(chunk.id)

    const now = Date.now()

    if (!controller) {
      controller = createController(chunk, resolvedOptions)
      threadControllers.set(chunk.id, controller)
      appendLoadingChunk(threadState, controller.skeleton)
    } else {
      mergeSkeleton(controller, chunk)
      const observedInterval = now - controller.lastPushAt
      if (observedInterval > 0) {
        controller.avgIntervalMs = clamp(
          controller.avgIntervalMs * (1 - resolvedOptions.emaAlpha) +
            observedInterval * resolvedOptions.emaAlpha,
          resolvedOptions.minDrainWindowMs,
          resolvedOptions.maxDrainWindowMs
        )
      }
    }

    const incomingSize = Math.max(1, getIncomingSize(chunk))
    controller.avgChunkChars = clamp(
      controller.avgChunkChars * (1 - resolvedOptions.emaAlpha) +
        incomingSize * resolvedOptions.emaAlpha,
      resolvedOptions.minReserveChars,
      resolvedOptions.maxReserveChars * 4
    )

    controller.lastPushAt = now
    controller.contentBuffer += chunk.content || ''
    controller.reasoningBuffer += chunk.reasoning_content || ''
    controller.additionalReasoningBuffer += chunk.additional_kwargs?.reasoning_content || ''

    if (Array.isArray(chunk.tool_call_chunks)) {
      chunk.tool_call_chunks.forEach((toolCallChunk) => {
        const index = toolCallChunk?.index
        if (index === undefined || index === null) return

        const existing = controller.toolCallArgBuffers.get(index) || { buffer: '' }
        existing.buffer += toolCallChunk.args || ''
        controller.toolCallArgBuffers.set(index, existing)
      })
    }

    const overflowBudget = getBufferedOverflow(getBufferedLength(controller), resolvedOptions)
    if (overflowBudget > 0) {
      if (controller.frameId !== null) {
        caf(controller.frameId)
      }
      controller.scheduled = false
      controller.frameId = null
      emitDelta(threadId, chunk.id, false, overflowBudget)
      return
    }

    schedule(threadId, chunk.id)
  }

  const flushThread = (threadId) => {
    const threadControllers = controllersByThread.get(threadId)
    if (!threadControllers) return

    threadControllers.forEach((controller, messageId) => {
      if (controller.frameId !== null) {
        caf(controller.frameId)
      }
      emitDelta(threadId, messageId, true)
    })
  }

  const resetThread = (threadId = null) => {
    if (threadId) {
      const threadControllers = controllersByThread.get(threadId)
      if (!threadControllers) return
      threadControllers.forEach((controller) => {
        if (controller.frameId !== null) {
          caf(controller.frameId)
        }
      })
      controllersByThread.delete(threadId)
      return
    }

    controllersByThread.forEach((threadControllers) => {
      threadControllers.forEach((controller) => {
        if (controller.frameId !== null) {
          caf(controller.frameId)
        }
      })
    })
    controllersByThread.clear()
  }

  return {
    pushChunk,
    flushThread,
    resetThread
  }
}
