import assert from 'node:assert/strict'
import test from 'node:test'
import { loadConfigFromFile } from 'vite'

test('开发代理只接管 API 路径，保留前端 API 服务页面', async () => {
  const { config } = await loadConfigFromFile({ command: 'serve', mode: 'development' })
  const proxy = Object.keys(config.server.proxy).find((key) => key.startsWith('^/api'))
  const pattern = new RegExp(proxy)
  for (const url of ['/api', '/api?check=1', '/api/system/ready', '/api/v1/agents/sessions']) {
    assert.equal(pattern.test(url), true, url)
  }
  for (const url of ['/api-services', '/api-services?tab=keys', '/apiary', '/agent']) {
    assert.equal(pattern.test(url), false, url)
  }
})
