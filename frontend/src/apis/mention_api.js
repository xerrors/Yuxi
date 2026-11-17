import { apiGet } from './base'

export const getSessionMentionPlugins = (threadId, signal) =>
  apiGet(`/api/mention/plugins?${new URLSearchParams({ thread_id: threadId })}`, { signal })

export const searchMentionSessions = (query, signal) =>
  apiGet(`/api/mention/sessions?${new URLSearchParams({ query })}`, { signal })

export const searchMentionFiles = (threadId, query, signal) => {
  const params = new URLSearchParams()
  if (threadId) params.set('thread_id', threadId)
  if (query) params.set('query', query)
  return apiGet(`/api/mention/search?${params.toString()}`, { signal })
}
