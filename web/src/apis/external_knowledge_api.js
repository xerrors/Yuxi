import { apiGet, apiPost, buildQuery } from './base'

const externalRoot = '/api/v1/knowledge/databases/external'

export const externalKnowledgeApi = {
  /** 列出当前用户可见的外部知识库。 */
  listDatabases: () => apiGet(externalRoot),

  /** 列出或按文件名搜索知识库文件。 */
  listFiles: (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(`${externalRoot}/${encodeURIComponent(kbId)}/files${query ? `?${query}` : ''}`)
  },

  /** 检索知识库片段。 */
  retrieve: (kbId, payload) => apiPost(`${externalRoot}/${encodeURIComponent(kbId)}/retrieve`, payload),

  /** 按行读取解析后的文件。 */
  openFile: (kbId, fileId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `${externalRoot}/${encodeURIComponent(kbId)}/files/${encodeURIComponent(fileId)}/open${query ? `?${query}` : ''}`
    )
  },

  /** 在文件内定位关键词或正则表达式。 */
  findFile: (kbId, fileId, payload) =>
    apiPost(`${externalRoot}/${encodeURIComponent(kbId)}/files/${encodeURIComponent(fileId)}/find`, payload)
}
