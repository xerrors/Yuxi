/** 按现有 API 顺序创建资源，并阻止未知提交结果被盲目重试。 */
export async function createAgentResources({ payload, servers, skillFile, progress, api }) {
  if (progress.uncertainStep) {
    throw new Error('上次创建请求结果未确认，请先核对已创建资源')
  }

  for (const slug of progress.createdMcpSlugs) {
    const retained = servers.find((server) => server.slug === slug)
    if (!retained || JSON.stringify(retained) !== JSON.stringify(progress.createdMcpConfigs[slug])) {
      throw new Error(`已创建 MCP「${slug}」不能从清单移除或修改`)
    }
  }

  if (servers.length && !progress.agent) {
    const existing = await api.listMcps()
    const occupied = new Set((existing.data || []).map((item) => item.slug))
    const conflict = servers.find((server) => !progress.createdMcpSlugs.includes(server.slug) && occupied.has(server.slug))
    if (conflict) throw new Error(`MCP「${conflict.slug}」已存在，请更换清单标识`)
  }

  /** 仅把未收到明确拒绝的写入标为结果未知。 */
  const createStep = async (label, action) => {
    try {
      return await action()
    } catch (error) {
      if (!Number.isInteger(error?.status) || error.status < 400 || error.status >= 500) {
        progress.uncertainStep = label
      }
      throw error
    }
  }

  for (const server of servers) {
    if (progress.createdMcpSlugs.includes(server.slug)) continue
    await createStep(`MCP「${server.slug}」`, () => api.createMcp(server))
    progress.createdMcpSlugs.push(server.slug)
    progress.createdMcpConfigs[server.slug] = server
  }

  if (!progress.agent) {
    if (servers.length) payload.config_json = { context: { mcps: servers.map((item) => item.slug) } }
    progress.agent = await createStep('智能体', () => api.createAgent(payload))
  }
  if (skillFile && !progress.skillUploaded) {
    await createStep('专属技能', () => api.uploadSkill(progress.agent.id, skillFile))
    progress.skillUploaded = true
  }
  return progress.agent
}
