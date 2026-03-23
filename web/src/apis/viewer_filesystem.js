import { apiGet } from './base'

const buildQuery = (params) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  })
  return query.toString()
}

const buildViewerQuery = (threadId, path, agentId = null, agentConfigId = null) => {
  return buildQuery({
    thread_id: threadId,
    path,
    agent_id: agentId,
    agent_config_id: agentConfigId
  })
}

export const getViewerFileSystemTree = (
  threadId,
  path = '/',
  agentId = null,
  agentConfigId = null
) => {
  const query = buildViewerQuery(threadId, path, agentId, agentConfigId)
  return apiGet(`/api/viewer/filesystem/tree?${query}`)
}

export const getViewerFileContent = (threadId, path, agentId = null, agentConfigId = null) => {
  const query = buildViewerQuery(threadId, path, agentId, agentConfigId)
  return apiGet(`/api/viewer/filesystem/file?${query}`)
}

export const downloadViewerFile = (threadId, path, agentId = null, agentConfigId = null) => {
  const query = buildViewerQuery(threadId, path, agentId, agentConfigId)
  return apiGet(`/api/viewer/filesystem/download?${query}`, {}, true, 'blob')
}
