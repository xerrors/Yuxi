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
  const release = (key) => releaseFilePreviewCacheEntry(cache, key)
  const invalidate = (path) => {
    release(filePreviewCacheKey(path, threadId.value))
    release(filePreviewCacheKey(path, 'workspace'))
  }
  const open = (file) => {
    if (!file?.path) return null
    const workspace = file.workspace === true
    const tab = { ...file, workspace, workdir: !workspace && file.workdir === true,
      path: String(file.path), name: file.name || String(file.path).split('/').pop() || String(file.path) }
    const index = tabs.value.findIndex((item) => item.path === tab.path)
    if (index < 0) tabs.value = [...tabs.value, tab]
    else {
      const existing = tabs.value[index]
      if ((!tab.workdir && !tab.workspace && tab.modified_at == null && tab.size == null) ||
        existing.modified_at !== tab.modified_at || existing.size !== tab.size ||
        Boolean(existing.workdir) !== tab.workdir || Boolean(existing.workspace) !== tab.workspace) invalidate(tab.path)
      tabs.value = tabs.value.map((item, current) => current === index ? { ...item, ...tab } : item)
    }
    return tab
  }
  const close = (path) => {
    invalidate(path)
    tabs.value = tabs.value.filter((tab) => tab.path !== path)
  }
  const closePath = (target) => {
    const removed = tabs.value.filter((tab) => includesPath(tab.path, target)).map((tab) => tab.path)
    for (const path of removed) close(path)
    for (const key of cache.keys()) {
      const separator = key.indexOf(':')
      const scope = key.slice(0, separator)
      if ([String(threadId.value), 'workspace'].includes(scope) && includesPath(key.slice(separator + 1), target)) release(key)
    }
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
