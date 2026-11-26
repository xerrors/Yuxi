import { onScopeDispose, reactive, ref, watch } from 'vue'
import { useUserStore } from '@/modules/identity'

import { filePreviewCacheKey, releaseFilePreviewCacheEntry, releaseFilePreviewCache } from './filePreviewCache'

/** 路径关闭包含子目录，不能误匹配相邻同前缀文件。 */
const includesPath = (path, target) => {
  const normalized = String(path || '').replace(/\/+$/, '')
  const prefix = String(target || '').replace(/\/+$/, '')
  return Boolean(normalized && prefix && (normalized === prefix || normalized.startsWith(`${prefix}/`)))
}

/** 文件预览的标签、缓存与对象 URL 随 Thread、身份和组件一起释放。 */
export function useFilePreviewSession(threadId) {
  const user = useUserStore()
  const tabs = ref([])
  const cache = reactive(new Map())
  const open = (file, sourceThreadId = threadId.value) => {
    if (!file?.path) return null
    const workspace = file.workspace === true
    if (!workspace && !sourceThreadId) return null
    const path = String(file.path)
    const key = filePreviewCacheKey(path, workspace ? 'workspace' : sourceThreadId)
    const tab = {
      ...file,
      key,
      threadId: workspace ? null : sourceThreadId,
      workspace,
      workdir: !workspace && file.workdir === true,
      path,
      name: file.name || path.split('/').pop() || path
    }
    const index = tabs.value.findIndex((item) => item.key === key)
    if (index < 0) tabs.value = [...tabs.value, tab]
    else {
      const existing = tabs.value[index]
      if ((!tab.workdir && !tab.workspace && tab.modified_at == null && tab.size == null) ||
        existing.modified_at !== tab.modified_at || existing.size !== tab.size ||
        Boolean(existing.workdir) !== tab.workdir) releaseFilePreviewCacheEntry(cache, key)
      tabs.value = tabs.value.map((item, current) => current === index ? { ...item, ...tab } : item)
    }
    return tab
  }
  const close = (key) => {
    releaseFilePreviewCacheEntry(cache, key)
    tabs.value = tabs.value.filter((tab) => tab.key !== key)
  }
  /** Viewer 删除只关闭操作所属 Thread 中的文件，保留其他作用域的同名资源。 */
  const closePath = (target, sourceThreadId) => {
    const removed = tabs.value
      .filter((tab) => tab.threadId === sourceThreadId && includesPath(tab.path, target))
      .map((tab) => tab.key)
    const prefix = `${sourceThreadId}:`
    for (const key of cache.keys()) {
      if (key.startsWith(prefix) && includesPath(key.slice(prefix.length), target)) {
        releaseFilePreviewCacheEntry(cache, key)
      }
    }
    tabs.value = tabs.value.filter((tab) => !removed.includes(tab.key))
    return removed
  }
  const reset = () => {
    releaseFilePreviewCache(cache)
    tabs.value = []
  }
  watch([threadId, () => user.token], reset, { flush: 'sync' })
  onScopeDispose(reset)
  return { tabs, cache, open, close, closePath, reset }
}
