// 流式期间每个开面板的页签按此间隔扫描线程目录；1s 在 40+ 并发页签下是最大的单一
// 请求扇出源（内网压测实测约 39rps 目录扫描）。5s 是感知与循环开销的折中；
// 新产物的即时列出由 Run 终态通知触发的面板刷新与在途去重门承接。
const FILESYSTEM_REFRESH_INTERVAL_MS = 5000

export const createFilesystemRefreshGate = () => {
  const inFlightThreads = new Set()
  const pendingThreads = new Set()
  return {
    begin(threadId, { ensure = false } = {}) {
      const key = String(threadId || '')
      if (!key) return false
      if (inFlightThreads.has(key)) {
        if (ensure) pendingThreads.add(key)
        return false
      }
      inFlightThreads.add(key)
      return true
    },
    finish(threadId) {
      const key = String(threadId || '')
      inFlightThreads.delete(key)
      return pendingThreads.delete(key)
    },
    canCommit(requestedThreadId, currentThreadId) {
      return Boolean(requestedThreadId) && requestedThreadId === currentThreadId
    }
  }
}

export const expandedKeysAfterFilesystemRefresh = (currentKeys, { silent }) =>
  silent ? currentKeys : []

const treeContainsKey = (nodes, targetKey) =>
  nodes.some(
    (node) =>
      node.key === targetKey || (node.children?.length && treeContainsKey(node.children, targetKey))
  )

const replaceTreeChildren = (nodes, targetKey, children) =>
  nodes.map((node) => {
    if (node.key === targetKey) return { ...node, children }
    if (!node.children?.length) return node
    return { ...node, children: replaceTreeChildren(node.children, targetKey, children) }
  })

export const refreshExpandedTree = async (
  nodes,
  expandedKeys,
  loadChildren,
  alreadyLoadedKeys = []
) => {
  let refreshed = nodes
  const loadedKeys = new Set(alreadyLoadedKeys)
  const directoryKeys = [...new Set(expandedKeys)].sort(
    (left, right) => String(left).split('/').length - String(right).split('/').length
  )
  for (const key of directoryKeys) {
    if (loadedKeys.has(key)) continue
    if (!treeContainsKey(refreshed, key)) continue
    try {
      refreshed = replaceTreeChildren(refreshed, key, await loadChildren(key))
    } catch {
      // 单个目录瞬时不可读时保留上一轮 children，不能清空整个已展开树。
    }
  }
  return refreshed
}

export const shouldRefreshActivePreview = (currentFile, latestFile) => {
  if (!currentFile) return false
  if (!latestFile) return false
  return (
    Number(currentFile.size ?? -1) !== Number(latestFile.size ?? -1) ||
    String(currentFile.modified_at || '') !== String(latestFile.modified_at || '')
  )
}

export const startAgentPanelFilesystemPolling = ({
  refresh,
  setIntervalFn = window.setInterval.bind(window),
  clearIntervalFn = window.clearInterval.bind(window)
}) => {
  const timer = setIntervalFn(() => {
    void refresh()
  }, FILESYSTEM_REFRESH_INTERVAL_MS)
  return () => clearIntervalFn(timer)
}
