import { apiAdminGet, apiAdminPost, apiAdminPut, apiGet, apiRequest, buildQuery } from './base'

/** 只接受已定义的站内媒体读取路径；授权头不能跟随来源字段访问其他地址。 */
export function validateMediaReadPath(path) {
  const reference =
    typeof path === 'string' &&
    path.match(
      /^\/api\/agent\/threads\/([^/?#\\]+)\/turns\/([^/?#\\]+)\/references\/([^/?#\\]+)\/media(?:\?asset_id=([^&#]+))?$/
    )
  const managed =
    typeof path === 'string' &&
    path.match(
      /^\/api\/knowledge\/knowledge-bases\/([^/?#\\]+)\/documents\/([^/?#\\]+)\/media\/([^/?#\\]+)\?revision=([^&#]+)$/
    )
  const match = reference || managed
  if (!match) throw new Error('媒体来源地址无效，无法读取。')
  for (const segment of match.slice(1).filter((value) => value !== undefined)) {
    let decoded
    try {
      decoded = decodeURIComponent(segment)
    } catch {
      throw new Error('媒体来源地址无效，无法读取。')
    }
    if (
      decoded === '.' ||
      decoded === '..' ||
      /[/?#\\]/.test(decoded) ||
      [...decoded].some((c) => c.charCodeAt(0) <= 32)
    )
      throw new Error('媒体来源地址无效，无法读取。')
  }
  return path
}

export const knowledgeMediaApi = {
  models: async (kbId) => {
    const endpoint = `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/media/models`
    try {
      return await apiAdminGet(endpoint)
    } catch (error) {
      if (error && typeof error === 'object') error.endpoint = `GET ${endpoint}`
      throw error
    }
  },
  documentImageAutoIndexSettings: (kbId) =>
    apiGet(
      `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/media/document-image-auto-index`
    ),
  updateDocumentImageAutoIndexSettings: (kbId, settings) =>
    apiAdminPut(
      `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/media/document-image-auto-index`,
      settings
    ),
  status: (kbId, fileId) =>
    apiAdminGet(
      `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(fileId)}/media-status`
    ),
  list: (kbId, fileId, offset = 0) =>
    apiAdminGet(
      `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/media?${buildQuery({ file_id: fileId, offset, limit: 100 })}`
    ),
  index: (kbId, fileId, config, documentImages = false) =>
    apiAdminPost(
      `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(fileId)}/media-index`,
      { config, document_images: documentImages }
    ),
  upload: (kbId, file, parentId, signal) => {
    const form = new FormData()
    form.append('file', file)
    if (parentId) form.append('parent_id', parentId)
    return apiRequest(`/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/media-upload`, {
      method: 'POST',
      body: form,
      signal
    })
  },
  assetUrl: (kbId, fileId, assetId, revision) =>
    `/api/knowledge/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(fileId)}/media/${encodeURIComponent(assetId)}?${buildQuery({ revision })}`,
  /** 请求经登录鉴权后才生成 Blob；实际 MIME 决定播放器，不猜文件名。 */
  readBlob: async (path, signal) => {
    validateMediaReadPath(path)
    const response = await apiRequest(
      path,
      { method: 'GET', signal, cache: 'no-store', redirect: 'error' },
      true,
      'blob'
    )
    const mime = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
    if (
      !['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'audio/wav', 'audio/x-wav', 'video/mp4'].includes(
        mime
      )
    )
      throw new Error('媒体格式不受支持，无法预览。')
    if (Number(response.headers.get('content-length')) > 10 * 1024 * 1024)
      throw new Error('媒体超过单次读取上限。')
    const blob = await response.blob()
    if (blob.size > 10 * 1024 * 1024) throw new Error('媒体超过单次读取上限。')
    return blob
  }
}
