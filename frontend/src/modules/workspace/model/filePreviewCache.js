/** 文件缓存键显式区分个人空间与 Thread。 */
export const filePreviewCacheKey = (path, threadId) => `${threadId}:${path}`

/** 缓存条目是对象 URL 的唯一所有者；删除先于重载发布。 */
export const releaseFilePreviewCacheEntry = (
  previewCache,
  cacheKey,
  revokeObjectURL = (url) => window.URL.revokeObjectURL(url)
) => {
  const cachedEntry = previewCache.get(cacheKey)
  if (!cachedEntry) return false

  previewCache.delete(cacheKey)
  if (cachedEntry.status === 'ready' && cachedEntry.file?.previewUrl) {
    revokeObjectURL(cachedEntry.file.previewUrl)
  }
  return true
}

export const reloadPreviewAfterOrderedCacheEntryInvalidation = async ({
  previewCache,
  cacheKey,
  revokeObjectURL,
  notifyPreviewChanged,
  reloadPreview
}) => {
  releaseFilePreviewCacheEntry(previewCache, cacheKey, revokeObjectURL)
  notifyPreviewChanged()
  await reloadPreview()
}

const entriesMatch = (a, b) => {
  if (a === b) return true
  if (!a || !b) return false
  if (a.promise && b.promise) return a.promise === b.promise
  if (a.file && b.file) return a.file === b.file
  return false
}

export const replacePreviewCacheEntryIfCurrent = (
  previewCache,
  cacheKey,
  currentEntry,
  nextEntry
) => {
  const existing = previewCache.get(cacheKey)
  if (!entriesMatch(existing, currentEntry)) return false
  if (nextEntry) previewCache.set(cacheKey, nextEntry)
  else releaseFilePreviewCacheEntry(previewCache, cacheKey)
  return true
}

export const settlePreviewCacheLoad = ({
  previewCache,
  cacheKey,
  loadingEntry,
  nextFile,
  lastAccessed,
  revokeObjectURL
}) => {
  const published = replacePreviewCacheEntryIfCurrent(previewCache, cacheKey, loadingEntry, {
    status: 'ready',
    file: nextFile,
    lastAccessed
  })
  if (!published && nextFile?.previewUrl) revokeObjectURL(nextFile.previewUrl)
  return published
}

/** 显式刷新只释放对应空间；全部重置由会话资源生命周期调用。 */
export const releaseFilePreviewCache = (cache, scope = null) => {
  for (const key of cache.keys()) {
    if (scope === null || key.startsWith(`${scope}:`)) releaseFilePreviewCacheEntry(cache, key)
  }
}

/** 预览缓存保持原有 12 个就绪条目上限，不淘汰正在阅读的条目。 */
export const pruneFilePreviewCache = (cache, activeKey) => {
  const entries = [...cache.entries()].filter(([, entry]) => entry.status === 'ready')
    .sort(([, left], [, right]) => (left.lastAccessed || 0) - (right.lastAccessed || 0))
  while (entries.length > 12) {
    const [key] = entries.shift()
    if (key !== activeKey) releaseFilePreviewCacheEntry(cache, key)
  }
}
