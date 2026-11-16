const RUN_SEQ_PATTERN = /^\d+-\d+$/

export const normalizeRunSeq = (value) => {
  if (value === undefined || value === null) return '0-0'
  const text = String(value).trim()
  return RUN_SEQ_PATTERN.test(text) ? text : '0-0'
}

const parseRunSeq = (value) => {
  const text = normalizeRunSeq(value)
  if (!text.includes('-')) {
    return { major: 0n, minor: 0n }
  }
  const [majorRaw, minorRaw] = text.split('-', 2)

  try {
    const major = BigInt(majorRaw || '0')
    const minor = BigInt(minorRaw || '0')
    return { major, minor }
  } catch {
    return { major: 0n, minor: 0n }
  }
}

export const compareRunSeq = (incoming, current) => {
  const left = parseRunSeq(incoming)
  const right = parseRunSeq(current)

  if (left.major > right.major) return 1
  if (left.major < right.major) return -1
  if (left.minor > right.minor) return 1
  if (left.minor < right.minor) return -1
  return 0
}

export const hasOngoingRunChunks = (threadState) => {
  const msgChunks = threadState?.onGoingConv?.msgChunks
  if (!msgChunks || typeof msgChunks !== 'object') return false
  return Object.values(msgChunks).some((chunks) =>
    Array.isArray(chunks) ? chunks.length > 0 : Boolean(chunks)
  )
}

export const resolveRunResumeAfterSeq = ({ snapshot, threadState }) => {
  if (!snapshot?.run_id || !hasOngoingRunChunks(threadState)) {
    return '0-0'
  }

  if (threadState?.activeRunId === snapshot.run_id) {
    return normalizeRunSeq(threadState.runLastSeq || snapshot.last_seq)
  }

  return '0-0'
}
