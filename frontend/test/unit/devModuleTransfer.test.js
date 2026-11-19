import assert from 'node:assert/strict'
import test from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'

test('开发源码和模型数据协商压缩，仅源码附映射，API 事件流直接交付', async (t) => {
  const event = 'data: ' + 'x'.repeat(4096) + '\n\n'
  const server = await createTestServer({
    server: { host: '127.0.0.1', port: 0 },
    plugins: [{
      name: 'fixture-events',
      configureServer(server) {
        server.middlewares.use('/api/events', (_req, res) => {
          res.setHeader('Content-Type', 'text/event-stream')
          res.end(event)
        })
      }
    }]
  })
  t.after(() => server.close())
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  const moduleUrl = origin + '/src/shared/lib/file_preview.js'
  const get = (url, encoding) => fetch(url, { headers: { 'Accept-Encoding': encoding } })
  const plain = await get(moduleUrl, 'identity')
  const compressed = await get(moduleUrl, 'gzip')
  assert.equal(plain.headers.get('Content-Encoding'), null)
  assert.equal(compressed.headers.get('Content-Encoding'), 'gzip')
  assert.match(compressed.headers.get('Vary'), /Accept-Encoding/i)
  const source = await plain.text()
  assert.match(source, /sourceMappingURL=data:/)
  assert.equal(await compressed.text(), source)
  const catalogUrl = origin + '/@id/__x00__virtual:model-display-metadata'
  const catalog = await get(catalogUrl, 'gzip')
  assert.equal(catalog.status, 200)
  assert.match(catalog.headers.get('Content-Type'), /javascript/)
  assert.equal(catalog.headers.get('Content-Encoding'), 'gzip')
  const catalogCode = await catalog.text()
  assert.equal(catalogCode.includes('sourceMappingURL='), false, '生成的模型数据不需要逐字段映射')
  const plainCatalog = await get(catalogUrl, 'identity')
  assert.ok(catalogCode === await plainCatalog.text(), '两种传输编码的模型数据应一致')
  const events = await get(origin + '/api/events', 'gzip')
  assert.equal(events.headers.get('Content-Encoding'), null)
  assert.equal(await events.text(), event)
})
