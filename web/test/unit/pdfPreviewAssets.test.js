import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { build, createServer } from 'vite'

const cmapPath = '/assets/pdfjs-cmaps/UniGB-UCS2-H.bcmap'

test('PDF 本地 CMap 开发响应和构建产物与依赖字节一致', async () => {
  const expected = await readFile(
    new URL('../../node_modules/pdfjs-dist/cmaps/UniGB-UCS2-H.bcmap', import.meta.url)
  )
  const component = await readFile(
    new URL('../../src/components/common/PdfPreview.vue', import.meta.url),
    'utf8'
  )
  assert.match(component, /cMapUrl: '\/assets\/pdfjs-cmaps\/'/)
  assert.doesNotMatch(component, /cdn\.jsdelivr/)
  const server = await createServer({ server: { host: '127.0.0.1', port: 0 } })
  try {
    await server.listen()
    const response = await fetch(`http://127.0.0.1:${server.httpServer.address().port}${cmapPath}`)
    assert.equal(response.status, 200)
    assert.match(response.headers.get('content-type'), /application\/octet-stream/)
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), expected)
  } finally {
    await server.close()
  }
  const result = await build({
    logLevel: 'silent',
    build: {
      write: false,
      minify: false,
      rollupOptions: {
        input: new URL('../../src/utils/pdfPreviewErrors.js', import.meta.url).pathname
      }
    }
  })
  const asset = result.output.find((entry) => entry.fileName === cmapPath.slice(1))
  assert.ok(asset, '构建产物必须包含本地 CMap')
  assert.deepEqual(Buffer.from(asset.source), expected)
})
