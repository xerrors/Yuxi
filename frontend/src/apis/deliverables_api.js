import { apiGet, buildQuery } from './base'

const BASE = '/api/workspace/deliverables'

export const deliverablesApi = {
  /** 分页查询当前用户的成果副本。 */
  list: ({ query = '', fileType = 'all', page = 1, pageSize = 20 } = {}) =>
    apiGet(`${BASE}?${buildQuery({ query, file_type: fileType, page, page_size: pageSize })}`),
  /** 读取成果的只读预览响应。 */
  preview: (id) => apiGet(`${BASE}/${encodeURIComponent(id)}/preview`, {}, true, 'blob'),
  /** 认证读取音视频等文件的完整字节。 */
  content: (id) => apiGet(`${BASE}/${encodeURIComponent(id)}/content`, {}, true, 'blob'),
  /** 认证下载成果副本并保留响应文件名。 */
  download: (id) => apiGet(`${BASE}/${encodeURIComponent(id)}/download`, {}, true, 'blob')
}
