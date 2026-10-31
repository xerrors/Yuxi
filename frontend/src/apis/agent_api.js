import { apiGet, apiPost, apiDelete, apiPut, apiRequest } from './base'
import { getApiAuthHeaders } from './auth_context'

/**
 * 智能体API模块
 * 包含智能体管理、聊天、配置等功能
 * 权限要求: 任何已登录用户（普通用户、管理员、超级管理员）
 */

// =============================================================================
// === 智能体聊天分组 ===
// =============================================================================

/** Web 来源与公开 item 分开读取，持久输入身份是唯一关联键。 */
async function attachInputOrigins(threadId, items) {
  const inputIds = [...new Set(items.filter((item) => item.role === 'user').map((item) => item.yuxi?.input_id).filter(Boolean))]
  if (!inputIds.length) return
  const params = new URLSearchParams()
  for (const id of inputIds) params.append('input_ids', id)
  const { origins } = await apiGet(`/api/agent/threads/${encodeURIComponent(threadId)}/input-origins?${params}`)
  for (const item of items) {
    if (item.role === 'user') item.yuxi.input_origin = origins[item.yuxi.input_id] || null
  }
}

export const agentApi = {
  /**
   * 获取智能体列表
   * @returns {Promise} - 智能体列表
   */
  getAgents: () => apiGet('/api/agent'),

  getPersonalAssistant: () => apiGet('/api/agents/personal'),
  getPersonalSessions: () => apiGet('/api/agents/personal/sessions'),
  createPersonalAssistant: (data) => apiPost('/api/agents/personal', data),
  updatePersonalAssistant: (data) => apiRequest('/api/agents/personal', { method: 'PATCH', body: JSON.stringify(data) }),

  getAgentBoundSkill: (slug) => apiGet(`/api/agent/${encodeURIComponent(slug)}/self-skill`),

  createAgentBoundSkill: (slug) => apiPost(`/api/agent/${encodeURIComponent(slug)}/self-skill`),

  uploadAgentBoundSkill: (slug, file) => {
    const body = new FormData()
    body.append('file', file)
    return apiPost(`/api/agent/${encodeURIComponent(slug)}/self-skill/upload`, body)
  },

  getAgentBackends: () => apiGet('/api/agent/backends'),

  getAgentBackendDetail: (backendId) =>
    apiGet(`/api/agent/backends/${encodeURIComponent(backendId)}`),

  /**
   * 获取单个智能体详情
   * @param {string} agentId - 智能体ID
   * @returns {Promise} - 智能体详情
   */
  getAgentDetail: (agentId) => apiGet(`/api/agent/${agentId}`),

  /** 直接读取公开内容页，轮次恢复可限定 Turn。 */
  getSessionItems: async (threadId, { after, turnId, limit = 100, order = 'desc' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit), order })
    if (after) params.set('after', after)
    const path = turnId ? `/turns/${encodeURIComponent(turnId)}/items` : '/items'
    const page = await apiGet(`/api/v1/agents/sessions/${threadId}${path}?${params}`)
    await attachInputOrigins(threadId, page.data)
    return page
  },

  getSessionReceipt: (threadId, key) =>
    apiGet(`/api/v1/agents/sessions/${threadId}/receipt?${new URLSearchParams({ idempotency_key: key })}`),

  /**
   * 获取会话内按数据库顺序排列的持久调试消息与 Run
   * @param {string} threadId - 会话ID
   * @returns {Promise<{audits: Array, truncated: boolean}>}
   */
  getThreadMessageAudits: (threadId) => apiGet(`/api/v1/agents/sessions/${threadId}/audits`),

  /** 批量恢复当前消息页的用户评价。 */
  getSessionFeedback: (threadId, runIds) => {
    const params = new URLSearchParams({ source: 'user', limit: '100' })
    for (const runId of runIds) params.append('run_ids', runId)
    return apiGet(`/api/agent/threads/${encodeURIComponent(threadId)}/feedback?${params}`)
  },

  putUserFeedback: (threadId, turnId, payload) =>
    apiPut(`/api/agent/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/feedback`, payload),

  deleteUserFeedback: (threadId, turnId, runId) =>
    apiDelete(`/api/agent/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/feedback?${new URLSearchParams({ run_id: runId })}`),

  /**
   * 获取指定会话的 AgentState
   * @param {string} agentId - 智能体ID
   * @param {string} threadId - 会话ID
   * @returns {Promise} - AgentState
   */
  controlSessionTree: (threadId, stopped, idempotencyKey) =>
    apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      { events: [{ type: stopped ? 'yuxi.session.tree.stop' : 'yuxi.session.tree.continue' }] },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),

  getAgentState: (threadId, { includeRelations = true } = {}) =>
    apiGet(
      `/api/v1/agents/sessions/${threadId}/state?include_relations=${includeRelations}`
    ),

  getCooperationSummary: (threadId) => apiGet(`/api/v1/agents/sessions/${threadId}/cooperation`),

  /**
   * 提交线程级主动上下文压缩
   */
  compressThreadContext: (threadId) => apiPost(`/api/v1/agents/sessions/${threadId}/compress`, {}),

  createAgent: (payload, skillFile = null) => {
    if (!skillFile) return apiPost('/api/agent', payload)
    const body = new FormData()
    body.append('agent', JSON.stringify(payload))
    body.append('file', skillFile)
    return apiPost('/api/agent/with-skill', body)
  },

  updateAgent: (agentId, payload) => apiPut(`/api/agent/${agentId}`, payload),

  deleteAgent: (agentId) => apiDelete(`/api/agent/${agentId}`),

  /** 产品对话以明确的 follow-up 或 steer 模式提交 Input。 */
  sendThreadMessage: (threadId, data) => {
    const content = [
      ...(data.query ? [{ type: 'input_text', text: data.query }] : []),
      ...(data.image_content || []).map((image) => ({
        type: 'input_image',
        image_url: image
      }))
    ]
    return apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      {
        events: [
          {
            type: 'agent.session.input.message',
            input: [{ role: 'user', content }],
            yuxi: {
              mode: data.mode,
              attachment_file_ids: data.attachment_file_ids || [],
              ...(data.mode !== 'steer'
                ? {
                    model: data.model_spec,
                    tool_approval_mode: data.tool_approval_mode
                  }
                : {})
            }
          }
        ]
      },
      { headers: { 'Idempotency-Key': data.idempotency_key } }
    )
  },

  resumeThreadTurn: (threadId, data) =>
    apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      {
        events: [
          {
            type: 'yuxi.session.input.resume',
            turn_id: data.turn_id,
            waitpoint_id: data.waitpoint_id,
            response: data.response
          }
        ]
      },
      { headers: { 'Idempotency-Key': data.idempotency_key } }
    ),

  cancelThreadTurn: (threadId, turnId, idempotencyKey, expectedRunId = null) =>
    apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      {
        events: [
          {
            type: 'agent.session.input.cancel',
            yuxi: { turn_id: turnId, ...(expectedRunId ? { expected_run_id: expectedRunId } : {}) }
          }
        ]
      },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),

  getPublicThread: (threadId) => apiGet(`/api/v1/agents/sessions/${threadId}`),

  getThreadTurn: (threadId, turnId, options = {}) =>
    apiGet(`/api/v1/agents/sessions/${threadId}/turns/${turnId}`, options),

  getTurnReferences: (threadId, turnId) =>
    apiGet(`/api/agent/threads/${threadId}/turns/${turnId}/references`),

  getTurnLangfuse: (threadId, turnId) =>
    apiGet(`/api/agent/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/langfuse`),

  annotateTurnReferences: (threadId, turnId) =>
    apiPost(`/api/agent/threads/${threadId}/turns/${turnId}/references`),

  getThreadInput: async (threadId, inputId, options = {}) => {
    const input = await apiGet(`/api/v1/agents/sessions/${threadId}/inputs/${inputId}`, options)
    await attachInputOrigins(threadId, input.items || [])
    return input
  },

  getSessionNavigation: (threadId) =>
    apiGet(`/api/agent/threads/${encodeURIComponent(threadId)}/navigation`),

  streamThreadEvents: (threadId, afterCursor = null, { signal } = {}) => {
    const headers = {
      ...getApiAuthHeaders()
    }
    if (afterCursor) headers['Last-Event-ID'] = afterCursor
    return fetch(`/api/v1/agents/sessions/${threadId}/events`, {
      method: 'GET',
      headers,
      signal
    })
  },

  getThreadQueue: (threadId) => apiGet(`/api/v1/agents/sessions/${threadId}/queue`),

  /**
   * 手动继续 failed/cancelled 后暂停的线程队列
   */
  continueThreadQueue: (threadId, idempotencyKey) =>
    apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      { events: [{ type: 'yuxi.session.input.continue' }] },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),

  /**
   * 取消排队中的请求
   */
  promoteThreadInput: (threadId, inputId, idempotencyKey) =>
    apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      { events: [{ type: 'yuxi.session.input.promote', input_id: inputId }] },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),

  cancelThreadInput: (threadId, inputId, idempotencyKey) =>
    apiPost(
      `/api/v1/agents/sessions/${threadId}/events`,
      { events: [{ type: 'yuxi.session.input.cancel_input', input_id: inputId }] },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    ),

  /**
   * 获取 Run 状态
   * @param {string} runId - run ID
   * @returns {Promise<Object>}
   */
  getAgentRun: (threadId, runId, options = {}) =>
    apiGet(`/api/v1/agents/sessions/${threadId}/runs/${runId}`, options)
}

// =============================================================================
// === 多模态图片支持分组 ===
// =============================================================================

export const multimodalApi = {
  /**
   * 上传图片并获取base64编码
   * @param {File} file - 图片文件
   * @returns {Promise} - 上传结果
   */
  uploadImage: (file) => {
    const formData = new FormData()
    formData.append('file', file)

    return apiRequest(
      '/api/agent/images',
      {
        method: 'POST',
        body: formData
      },
      true
    )
  }
}

// =============================================================================
// === 对话线程分组 ===
// =============================================================================

export const threadApi = {
  /**
   * 获取对话线程列表
   * @param {string | null | undefined} agentId - 智能体ID，可选；不传时返回全部智能体对话
   * @param {number} limit - 返回数量限制，默认100
   * @param {string | null} after - 上一页的 last_id
   * @returns {Promise} - Session 资源分页
   */
  getThreads: (agentId = null, limit = 100, after = null, { isPinned = null } = {}) => {
    const params = new URLSearchParams({
      limit: String(limit)
    })
    if (after) params.set('after', after)
    if (isPinned !== null) params.set('is_pinned', String(isPinned))
    if (agentId) {
      params.set('agent_id', agentId)
    }
    const url = `/api/v1/agents/sessions?${params.toString()}`
    return apiGet(url)
  },

  /**
   * 搜索历史对话
   * @param {string} query - 搜索关键词
   * @param {Object} options - 搜索选项
   * @param {string | null | undefined} options.agentId - 智能体ID，可选
   * @param {number} options.limit - 返回数量限制
   * @param {number} options.offset - 偏移量
   * @returns {Promise} - 搜索结果
   */
  searchThreads: (query, { agentId = null, limit = 20, offset = 0 } = {}) => {
    const params = new URLSearchParams({
      q: query,
      limit: String(limit),
      offset: String(offset)
    })
    if (agentId) {
      params.set('agent_id', agentId)
    }
    return apiGet(`/api/v1/agents/sessions/search?${params.toString()}`)
  },

  /**
   * 创建新对话线程
   * @param {string} agentId - 智能体ID
   * @param {string} title - 对话标题
   * @param {Object} metadata - 元数据
   * @returns {Promise} - 创建结果
   */
  createThread: async (agentId, title, metadata, { requestId, projectId } = {}) => {
    const session = await apiPost(
      '/api/v1/agents/sessions',
      {
        agent_id: agentId,
        title: title ? Array.from(title).slice(0, 255).join('') : null,
        tool_approval_mode: metadata?.tool_approval_mode,
        ...(projectId ? { project_id: projectId } : {})
      },
      { headers: { 'Idempotency-Key': requestId } }
    )
    return session
  },

  /**
   * 更新对话线程
   * @param {string} threadId - 对话线程ID
   * @param {string} title - 对话标题
   * @param {boolean} is_pinned - 是否置顶
   * @param {string} toolApprovalMode - 工具审批模式
   * @returns {Promise} - 更新结果
   */
  updateThread: (threadId, title, is_pinned, toolApprovalMode, modelSpec) =>
    apiRequest(`/api/v1/agents/sessions/${threadId}`, {
      method: 'POST',
      body: JSON.stringify({
        yuxi: { title, is_pinned, tool_approval_mode: toolApprovalMode },
        ...(modelSpec ? { agent: { model: modelSpec } } : {})
      })
    }),

  /**
   * 记录用户已查看该线程的最新顶层 run，清除侧边栏未读状态
   * @param {string} threadId - 对话线程ID
   * @returns {Promise} - 更新后的线程
   */
  markThreadViewed: (threadId) => apiPost(`/api/v1/agents/sessions/${threadId}/viewed`),

  /**
   * 删除对话线程
   * @param {string} threadId - 对话线程ID
   * @returns {Promise} - 删除结果
   */
  archiveThread: (threadId) => apiPost(`/api/v1/agents/sessions/${threadId}/archive`),

  /**
   * 获取线程附件列表
   * @param {string} threadId - 对话线程ID
   * @returns {Promise}
   */
  getThreadAttachments: (threadId) => apiGet(`/api/v1/agents/sessions/${threadId}/attachments`),

  /**
   * 获取线程文件下载/预览 URL
   * @param {string} threadId
   * @param {string} path
   * @param {boolean} download
   * @returns {string}
   */
  getThreadArtifactUrl: (threadId, path, download = false) => {
    const encodedPath = path
      .split('/')
      .filter(Boolean)
      .map((segment) => encodeURIComponent(segment))
      .join('/')
    const query = download ? '?download=true' : ''
    return `/api/v1/agents/sessions/${threadId}/artifacts/${encodedPath}${query}`
  },

  /**
   * 下载线程文件（带鉴权）
   * @param {string} threadId
   * @param {string} path
   * @returns {Promise<Response>}
   */
  downloadThreadArtifact: (threadId, path) =>
    apiGet(threadApi.getThreadArtifactUrl(threadId, path, true), {}, true, 'blob'),

  /** 读取允许跨 Project/User Data/Skills 的 artifact 预览字节。 */
  previewThreadArtifact: (threadId, path) =>
    apiGet(
      `${threadApi.getThreadArtifactUrl(threadId, path, false)}?preview=true`,
      {},
      true,
      'blob'
    ),

  /**
   * 保存交付物到指定 workspace 目录
   * @param {string} threadId
   * @param {string} path
   * @param {string} destinationPath
   * @returns {Promise}
   */
  saveThreadArtifactToWorkspace: (threadId, path, destinationPath) =>
    apiPost(`/api/v1/agents/sessions/${threadId}/artifacts/save`, {
      path,
      destination_path: destinationPath
    }),

  /** 上传为独立 draft，发送时提交返回的文件 ID。 */
  uploadDraftFile: (file) => {
    const body = new FormData()
    body.append('file', file)
    return apiRequest('/api/v1/agents/files', { method: 'POST', body })
  },

  getDraftFile: (fileId) => apiGet(`/api/agent/files/${fileId}`),

  parseDraftFile: (fileId, parseMethod) =>
    apiPost(`/api/agent/files/${fileId}/parse`, { parse_method: parseMethod }),

  deleteDraftFile: (fileId) => apiDelete(`/api/v1/agents/files/${fileId}`),

  /**
   * 删除附件
   * @param {string} threadId
   * @param {string} fileId
   * @returns {Promise}
   */
  deleteThreadAttachment: (threadId, fileId) =>
    apiDelete(`/api/v1/agents/sessions/${threadId}/attachments/${fileId}`)
}
