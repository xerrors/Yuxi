import assert from 'node:assert/strict'
import test from 'node:test'

import { parseMcpManifest } from '../../src/utils/mcpManifest.js'

test('将远程 mcpServers 映射到创建请求', () => {
  assert.deepEqual(parseMcpManifest(JSON.stringify({
    mcpServers: { search: { type: 'http', url: 'https://example.com/mcp', headers: { Authorization: 'Bearer test' } } }
  })), [{
    slug: 'search', name: 'search', transport: 'streamable_http',
    url: 'https://example.com/mcp', headers: { Authorization: 'Bearer test' }
  }])
})

test('不支持的本地进程配置在任何创建请求前被拒绝', () => {
  assert.throws(() => parseMcpManifest(JSON.stringify({
    mcpServers: {
      remote: { type: 'sse', url: 'https://example.com/sse' },
      local: { command: 'npx', args: ['server'] }
    }
  })), /不支持的字段/)
})

test('不接受无 URL 或非 HTTP URL 的清单', () => {
  assert.throws(() => parseMcpManifest('{"mcpServers":{"bad":{"type":"http"}}}'), /HTTP URL/)
  assert.throws(() => parseMcpManifest(JSON.stringify({ mcpServers: { bad: { type: 'http', url: 'file:///tmp/x' } } })), /HTTP URL/)
})

test('超出 MCP 数据字段长度的清单在提交前被拒绝', () => {
  assert.throws(() => parseMcpManifest(JSON.stringify({
    mcpServers: { bad: { type: 'http', url: 'https://example.com/mcp', name: 'x'.repeat(101) } }
  })), /name.*100/)
})
