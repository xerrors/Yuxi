import assert from 'node:assert/strict'
import test from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'

const officeResponse = (bytes, headers) =>
  new Response(bytes, {
    headers: {
      'content-type': 'application/octet-stream',
      ...headers
    }
  })

test('Office 原文件变成带扩展名的 File，不创建对象 URL', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true } })
  t.after(() => server.close())
  const preview = await server.ssrLoadModule('/src/shared/lib/file_preview.js')
  const created = []
  globalThis.window = { URL: { createObjectURL: (blob) => { created.push(blob); return 'blob:office' } } }
  t.after(() => { delete globalThis.window })

  assert.equal(preview.getPreviewTypeByPath('预算.xlsx'), 'office')
  assert.equal(preview.getPreviewTypeByPath('legacy.XLS'), 'office')
  assert.equal(preview.getPreviewTypeByPath('报告.docx'), 'office')
  assert.equal(preview.getPreviewTypeByPath('slides.pptx'), 'office')
  assert.equal(preview.getPreviewTypeByPath('notes.csv'), 'text')
  assert.equal(preview.getPreviewTypeByPath('report.pdf'), 'pdf')
  assert.equal(preview.getPreviewTypeByPath('readme.md'), 'markdown')

  const office = await preview.normalizePreviewResponse(
    officeResponse(new Uint8Array([1, 2, 3, 4]), {
      'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'x-yuxi-preview-type': 'office',
      'x-yuxi-preview-filename': encodeURIComponent('预算.xlsx')
    }),
    { path: '/files/ignored.bin', filename: 'ignored.bin' }
  )
  assert.equal(office.previewType, 'office')
  assert.equal(office.previewUrl, '')
  assert.equal(office.previewFile.name, '预算.xlsx')
  assert.equal(office.previewFile instanceof File, true)
  assert.deepEqual(created, [])

  const pdf = await preview.normalizePreviewResponse(
    officeResponse(new Uint8Array([37, 80, 68, 70]), {
      'content-type': 'application/pdf'
    }),
    { path: '/files/report.pdf' }
  )
  assert.equal(pdf.previewType, 'pdf')
  assert.equal(pdf.previewUrl, 'blob:office')
  assert.equal(pdf.previewFile, undefined)
  assert.equal(created.length, 1)

  const markdown = await preview.normalizePreviewResponse(
    new Response(JSON.stringify({ content: '# 标题', preview_type: 'markdown', supported: true }), {
      headers: { 'content-type': 'application/json' }
    }),
    { path: '/files/readme.md' }
  )
  assert.equal(markdown.previewType, 'markdown')
  assert.equal(markdown.content, '# 标题')
  assert.equal(markdown.previewUrl, '')
})

test('缓存释放 Office 原文件时不撤销空的对象 URL', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true } })
  t.after(() => server.close())
  const cache = await server.ssrLoadModule('/src/modules/workspace/model/filePreviewCache.js')
  const previewCache = new Map([
    ['workspace:/预算.xlsx', { status: 'ready', file: { previewUrl: '', previewFile: new File(['x'], '预算.xlsx') } }]
  ])
  const revoked = []
  assert.equal(
    cache.releaseFilePreviewCacheEntry(previewCache, 'workspace:/预算.xlsx', (url) => revoked.push(url)),
    true
  )
  assert.deepEqual(revoked, [])
  assert.equal(previewCache.has('workspace:/预算.xlsx'), false)
})
