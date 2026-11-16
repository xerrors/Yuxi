import { apiGet } from './base'

export const searchMentionFiles = (threadId, query, signal) => {
  const params = new URLSearchParams()
  if (threadId) params.set('thread_id', threadId)
  if (query) params.set('query', query)
  return apiGet(`/api/mention/search?${params.toString()}`, { signal })
}
