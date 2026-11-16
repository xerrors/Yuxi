const cloneChunk = (value) => {
  if (typeof structuredClone === 'function') return structuredClone(value)
  return JSON.parse(JSON.stringify(value))
}
const hasText = (value) => typeof value === 'string' && value.length > 0
const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

const START_BUFFER_MS = 180
const RATE_SAMPLE_MS = 200
const RATE_ADJUST_MS = 300
const CATCH_UP_MS = 600
const MIN_CHARS_PER_SECOND = 32

/** 清空文本字段，保留同一消息的身份与元数据。 */
const stripBufferedFields = (chunk) => {
  const stripped = cloneChunk(chunk)
  stripped.content = ''
  stripped.reasoning_content = ''
  return stripped
}

const appendLoadingChunk = (threadState, chunk) => {
  if (!threadState || !chunk?.id) return
  const chunks = threadState.onGoingConv.msgChunks
  if (!chunks[chunk.id]) chunks[chunk.id] = []
  chunks[chunk.id].push(chunk)
}

const raf =
  typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'
    ? (callback) => window.requestAnimationFrame(callback)
    : (callback) => setTimeout(callback, 16)

const caf =
  typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function'
    ? (id) => window.cancelAnimationFrame(id)
    : (id) => clearTimeout(id)

const getBufferedLength = (controller) =>
  controller.contentBuffer.length + controller.reasoningBuffer.length

/** 预算以 UTF-16 长度计量，实际切片停在完整字素边界。 */
const takeFromBuffer = (value, count) => {
  if (!value || count <= 0) return { emitted: '', rest: value }
  if (count >= value.length) return { emitted: value, rest: '' }
  let end = 0
  for (const { segment, index } of segmenter.segment(value)) {
    if (index + segment.length > count && end > 0) break
    end = index + segment.length
    if (end >= count) break
  }
  return { emitted: value.slice(0, end), rest: value.slice(end) }
}

/** 在轮询批次之间平稳播放正文；终态和工具语义由调用方及时推进。 */
export function useStreamSmoother({ getThreadState }) {
  const controllersByThread = new Map()

  /** 同步交付剩余文本，同时取消此消息唯一的帧任务。 */
  const flushMessage = (threadId, messageId) => {
    const controllers = controllersByThread.get(threadId)
    const controller = controllers?.get(messageId)
    if (!controller) return
    if (controller.frameId !== null) caf(controller.frameId)
    if (getBufferedLength(controller) > 0) {
      const delta = stripBufferedFields(controller.skeleton)
      delta.content = controller.contentBuffer
      delta.reasoning_content = controller.reasoningBuffer
      appendLoadingChunk(getThreadState(threadId), delta)
    }
    controllers.delete(messageId)
  }

  /** 播放速度按时间平滑变化，避免包大小或刷新率直接决定出字速度。 */
  const tick = (threadId, messageId) => {
    const controllers = controllersByThread.get(threadId)
    const controller = controllers?.get(messageId)
    if (!controller) return
    controller.frameId = null
    const threadState = getThreadState(threadId)
    if (!threadState) {
      controllers.delete(messageId)
      return
    }

    const now = performance.now()
    if (now > controller.lastFrameAt) {
      // 页面恢复或长任务之后不把累计帧时间一次兑换为大量正文。
      const elapsed = Math.min(now - controller.lastFrameAt, 64)
      controller.lastFrameAt = now
      const targetRate = Math.max(
        MIN_CHARS_PER_SECOND,
        controller.arrivalRate,
        (getBufferedLength(controller) * 1000) / CATCH_UP_MS
      )
      if (controller.rate === 0) controller.rate = targetRate
      controller.rate += (targetRate - controller.rate) * (1 - Math.exp(-elapsed / RATE_ADJUST_MS))
      controller.credit += (controller.rate * elapsed) / 1000

      const budget = Math.floor(controller.credit)
      let remaining = budget
      if (remaining > 0) {
        const delta = stripBufferedFields(controller.skeleton)
        for (const [bufferKey, field] of [
          ['contentBuffer', 'content'],
          ['reasoningBuffer', 'reasoning_content']
        ]) {
          const part = takeFromBuffer(controller[bufferKey], remaining)
          controller[bufferKey] = part.rest
          remaining -= part.emitted.length
          delta[field] = part.emitted
        }
        controller.credit -= budget - remaining
        appendLoadingChunk(threadState, delta)
      }
    }

    if (getBufferedLength(controller) > 0) {
      controller.frameId = raf(() => tick(threadId, messageId))
    } else {
      controllers.delete(messageId)
    }
  }

  /** 累积正文；工具参数及减少动态效果模式保持即时呈现。 */
  const pushChunk = (chunk, threadId) => {
    const threadState = getThreadState(threadId)
    if (!threadState || !chunk?.id) return
    const content = chunk.content || ''
    const reasoning = chunk.reasoning_content || ''
    const hasPayload = hasText(content) || hasText(reasoning)
    const reduceMotion =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (chunk.tool_call_chunks?.length || reduceMotion) {
      flushMessage(threadId, chunk.id)
      appendLoadingChunk(threadState, chunk)
      return
    }
    if (!hasPayload) {
      appendLoadingChunk(threadState, chunk)
      return
    }

    if (!controllersByThread.has(threadId)) controllersByThread.set(threadId, new Map())
    const controllers = controllersByThread.get(threadId)
    let controller = controllers.get(chunk.id)
    const now = performance.now()
    if (!controller) {
      controller = {
        skeleton: stripBufferedFields(chunk),
        contentBuffer: '',
        reasoningBuffer: '',
        frameId: null,
        lastFrameAt: now + START_BUFFER_MS,
        sampleAt: now,
        sampleChars: 0,
        arrivalRate: 0,
        rate: 0,
        credit: 0
      }
      controllers.set(chunk.id, controller)
      appendLoadingChunk(threadState, controller.skeleton)
    } else {
      const stripped = stripBufferedFields(chunk)
      controller.skeleton = {
        ...controller.skeleton,
        ...stripped
      }
    }

    controller.contentBuffer += content
    controller.reasoningBuffer += reasoning
    controller.sampleChars += content.length + reasoning.length
    const sampleMs = now - controller.sampleAt
    if (sampleMs >= RATE_SAMPLE_MS) {
      const observedRate = (controller.sampleChars * 1000) / sampleMs
      const weight = 1 - Math.exp(-sampleMs / RATE_ADJUST_MS)
      controller.arrivalRate += (observedRate - controller.arrivalRate) * weight
      controller.sampleChars = 0
      controller.sampleAt = now
    }
    if (controller.frameId === null) controller.frameId = raf(() => tick(threadId, chunk.id))
  }

  /** 结束、取消和审批前同步交付对应线程全部缓冲。 */
  const flushThread = (threadId) => {
    const controllers = controllersByThread.get(threadId)
    if (!controllers) return
    for (const messageId of controllers.keys()) flushMessage(threadId, messageId)
    controllersByThread.delete(threadId)
  }

  /** 清除指定线程或全部线程的延迟任务，不再向旧消息写入。 */
  const resetThread = (threadId = null) => {
    for (const [id, controllers] of controllersByThread) {
      if (threadId && id !== threadId) continue
      for (const controller of controllers.values()) {
        if (controller.frameId !== null) caf(controller.frameId)
      }
      controllersByThread.delete(id)
    }
  }

  return { pushChunk, flushThread, resetThread }
}
