/** 将远程 mcpServers 清单转换为 Yuxi 的 MCP 创建请求。 */
export function parseMcpManifest(text) {
  if (!text.trim()) return []

  let manifest
  try {
    manifest = JSON.parse(text)
  } catch {
    throw new Error('MCP 清单不是有效的 JSON')
  }
  const servers = manifest?.mcpServers
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
    throw new Error('MCP 清单需要包含 mcpServers 对象')
  }

  return Object.entries(servers).map(([slug, config]) => {
    if (!slug || slug.length > 100 || !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(slug)) {
      throw new Error('MCP 标识只能包含字母、数字、连字符和下划线，且不能超过 100 字符')
    }
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      throw new Error(`MCP「${slug}」的配置必须是对象`)
    }
    const allowed = new Set([
      'type', 'transport', 'url', 'name', 'description', 'headers',
      'timeout', 'sse_read_timeout', 'tags', 'icon'
    ])
    if (Object.keys(config).some((key) => !allowed.has(key))) {
      throw new Error(`MCP「${slug}」包含不支持的字段；仅支持远程 HTTP 或 SSE 服务`)
    }

    const transport = config.transport || config.type
    const normalizedTransport = transport === 'http' ? 'streamable_http' : transport
    if (!['sse', 'streamable_http'].includes(normalizedTransport)) {
      throw new Error(`MCP「${slug}」只支持 sse 或 streamable_http`)
    }
    let url
    try {
      url = new URL(config.url)
    } catch {
      throw new Error(`MCP「${slug}」需要有效的 HTTP URL`)
    }
    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error(`MCP「${slug}」需要有效的 HTTP URL`)
    }
    if (url.toString().length > 500) {
      throw new Error(`MCP「${slug}」的 URL 不能超过 500 字符`)
    }
    if (config.type && config.transport && config.type !== config.transport &&
      !(config.type === 'http' && config.transport === 'streamable_http')) {
      throw new Error(`MCP「${slug}」的 type 与 transport 不一致`)
    }
    for (const [key, limit] of [['name', 100], ['description', 500], ['icon', 50]]) {
      if (config[key] !== undefined && (
        typeof config[key] !== 'string' || config[key].length > limit
      )) {
        throw new Error(`MCP「${slug}」的 ${key} 必须是不超过 ${limit} 字符的字符串`)
      }
    }
    if (config.tags !== undefined && (
      !Array.isArray(config.tags) || config.tags.some((tag) => typeof tag !== 'string')
    )) {
      throw new Error(`MCP「${slug}」的 tags 必须是字符串数组`)
    }
    if (config.headers !== undefined && (
      !config.headers || typeof config.headers !== 'object' || Array.isArray(config.headers) ||
      Object.values(config.headers).some((value) => typeof value !== 'string')
    )) {
      throw new Error(`MCP「${slug}」的 headers 必须是字符串键值对象`)
    }
    for (const key of ['timeout', 'sse_read_timeout']) {
      if (config[key] !== undefined && (
        !Number.isInteger(config[key]) || config[key] <= 0 || config[key] > 2147483647
      )) {
        throw new Error(`MCP「${slug}」的 ${key} 必须是正整数`)
      }
    }

    return {
      slug,
      name: config.name || slug,
      transport: normalizedTransport,
      url: url.toString(),
      ...(config.description !== undefined && { description: config.description }),
      ...(config.headers !== undefined && { headers: config.headers }),
      ...(config.timeout !== undefined && { timeout: config.timeout }),
      ...(config.sse_read_timeout !== undefined && { sse_read_timeout: config.sse_read_timeout }),
      ...(config.tags !== undefined && { tags: config.tags }),
      ...(config.icon !== undefined && { icon: config.icon })
    }
  })
}
