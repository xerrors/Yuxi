import { apiDelete, apiGet, apiPost, apiPut, buildQuery } from './base'

export const getWorkspaceTree = (
  path = '/',
  recursive = false,
  filesOnly = false,
  includeUnboundProjectDirs = false
) => {
  const query = buildQuery({
    path,
    recursive,
    files_only: filesOnly,
    include_unbound_project_dirs: includeUnboundProjectDirs || undefined
  })
  return apiGet(`/api/workspace/tree?${query}`)
}

export const getWorkspaceFileContent = (path) => {
  const query = buildQuery({ path })
  return apiGet(`/api/workspace/file?${query}`, {}, true, 'blob')
}

export const getWorkspaceKnowledgeTree = (kbId, params = {}) => {
  const query = buildQuery({
    kb_id: kbId,
    parent_id: params.parentId,
    path_prefix: params.pathPrefix,
    page: params.page,
    page_size: params.pageSize,
    recursive: params.recursive || false,
    files_only: params.filesOnly || false
  })
  return apiGet(`/api/workspace/knowledge/tree?${query}`)
}

export const getWorkspaceKnowledgeFileContent = (kbId, fileId) => {
  const query = buildQuery({ kb_id: kbId, file_id: fileId })
  return apiGet(`/api/workspace/knowledge/file?${query}`, {}, true, 'blob')
}

export const downloadWorkspaceKnowledgeFile = (kbId, fileId, variant = 'original') => {
  const query = buildQuery({ kb_id: kbId, file_id: fileId, variant })
  return apiGet(`/api/workspace/knowledge/download?${query}`, {}, true, 'blob')
}

export const saveWorkspaceFileContent = (path, content) => {
  return apiPut('/api/workspace/file', { path, content })
}

export const deleteWorkspacePath = (path) => {
  const query = buildQuery({ path })
  return apiDelete(`/api/workspace/file?${query}`)
}

export const createWorkspaceDirectory = (parentPath, name) => {
  return apiPost('/api/workspace/directory', {
    parent_path: parentPath,
    name
  })
}

export const uploadWorkspaceFiles = (parentPath, files) => {
  const formData = new FormData()
  formData.append('parent_path', parentPath)
  files.forEach((file) => formData.append('files', file))
  return apiPost('/api/workspace/upload', formData)
}

export const downloadWorkspaceFile = (path) => {
  const query = buildQuery({ path })
  return apiGet(`/api/workspace/download?${query}`, {}, true, 'blob')
}

export const searchWorkspaceFiles = (query) => {
  return apiGet(`/api/workspace/search?${buildQuery({ query })}`)
}
