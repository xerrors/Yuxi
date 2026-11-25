import { apiDelete, apiGet, buildQuery } from './base'

const buildViewerQuery = (threadId, path) => {
  return buildQuery({
    thread_id: threadId,
    path
  })
}

export const getViewerFileSystemTree = (threadId, path = '/') => {
  const query = buildViewerQuery(threadId, path)
  return apiGet(`/api/viewer/filesystem/tree?${query}`)
}

export const getViewerFileContent = (threadId, path) => {
  const query = buildViewerQuery(threadId, path)
  return apiGet(`/api/viewer/filesystem/file?${query}`, {}, true, 'blob')
}

export const downloadViewerFile = (threadId, path) => {
  const query = buildViewerQuery(threadId, path)
  return apiGet(`/api/viewer/filesystem/download?${query}`, {}, true, 'blob')
}

export const deleteViewerFile = (threadId, path) => {
  const query = buildViewerQuery(threadId, path)
  return apiDelete(`/api/viewer/filesystem/file?${query}`)
}

export const searchViewerFiles = (threadId, query) => {
  const queryStr = buildQuery({ thread_id: threadId, query })
  return apiGet(`/api/viewer/filesystem/search?${queryStr}`)
}
