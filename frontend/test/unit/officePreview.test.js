import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'

const CDN_PATTERN = /cdn\.jsdelivr|unpkg\.com|cdnjs\.cloudflare|fonts\.googleapis|fonts\.gstatic/i
const FORBIDDEN_PACKAGES = [
  '@file-viewer/vue3-full',
  '@file-viewer/preset-all',
  '@file-viewer/preset-office',
  '@file-viewer/renderer-pdf',
  '@file-viewer/renderer-cad',
  '@file-viewer/renderer-3d',
  '@file-viewer/renderer-archive',
  '@file-viewer/renderer-ppt'
]

const officeResponse = (bytes, headers) =>
  new Response(bytes, {
    headers: {
      'content-type': 'application/octet-stream',
      ...headers
    }
  })

test('Office 预览只装配指定渲染器，源码不引用外部 CDN', async () => {
  const packageJson = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8'))
  const names = new Set([
    ...Object.keys(packageJson.dependencies || {}),
    ...Object.keys(packageJson.devDependencies || {})
  ])
  for (const name of ['@file-viewer/vue3', '@file-viewer/renderer-word', '@file-viewer/renderer-spreadsheet', '@file-viewer/renderer-pptx', '@file-viewer/vite-plugin']) {
    assert.equal(names.has(name), true, name)
  }
  for (const name of FORBIDDEN_PACKAGES) {
    assert.equal(names.has(name), false, name)
  }

  const sources = await Promise.all(
    [
      '../../src/shared/ui/OfficePreview.vue',
      '../../src/modules/session/ui/workspace/officePdfFallback.js',
      '../../src/shared/lib/file_preview.js',
      '../../vite.config.js'
    ].map((path) => readFile(new URL(path, import.meta.url), 'utf8'))
  )
  const viteConfig = sources[3]
  assert.match(viteConfig, /inject:\s*false/)
  assert.match(viteConfig, /copyAssets:\s*true/)
  assert.match(viteConfig, /formats:\s*\[[^\]]*docx[^\]]*xlsx[^\]]*xls[^\]]*pptx/)
  assert.doesNotMatch(viteConfig, /preset-all|vue3-full|preset:\s*'office'|preset:\s*'all'/)
  for (const source of sources) {
    assert.doesNotMatch(source, CDN_PATTERN)
    assert.doesNotMatch(source, /:url=|url:\s*['"]https?:/)
  }
  assert.match(sources[0], /virtual:file-viewer-renderers/)
  assert.match(sources[0], /:file="file"/)
  assert.doesNotMatch(sources[0], /<component[\s\S]*\burl=/)
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
  assert.equal(preview.isOfficePdfFallback('报告.docx'), true)
  assert.equal(preview.isOfficePdfFallback('预算.xlsx'), false)

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

  const fromPath = await preview.normalizePreviewResponse(
    officeResponse(new Uint8Array([9]), { 'content-type': 'application/octet-stream' }),
    { path: '/files/sheet.xlsx' }
  )
  assert.equal(fromPath.previewType, 'office')
  assert.equal(fromPath.previewFile.name, 'sheet.xlsx')
  assert.equal(fromPath.previewUrl, '')

  const pdf = await preview.normalizePreviewResponse(
    officeResponse(new Uint8Array([37, 80, 68, 70]), {
      'content-type': 'application/pdf',
      'x-yuxi-preview-type': 'pdf'
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

test('Office PDF 回退走已有鉴权接口，且不把地址交给预览器', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true } })
  t.after(() => server.close())
  const auth = await server.ssrLoadModule('/src/apis/auth_context.js')
  auth.configureApiAuth({ getToken: () => 'test-token' })
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), authorization: options?.headers?.Authorization || '' })
    return new Response(new Uint8Array([37, 80, 68, 70]), {
      status: 200,
      headers: { 'content-type': 'application/pdf', 'x-yuxi-preview-type': 'pdf' }
    })
  }
  t.after(() => { delete globalThis.fetch })
  const office = await server.ssrLoadModule('/src/modules/session/ui/workspace/officePdfFallback.js')

  await office.loadOfficePdfFallback({ kb_id: 'kb-1', file_id: 'file-1', path: '/wrong.docx' })
  await office.loadOfficePdfFallback({ workspace: true, path: '/notes/报告.docx', threadId: 'thread-1' })
  await office.loadOfficePdfFallback({ workdir: true, threadId: 'thread-1', path: '/slides.pptx' })
  await office.loadOfficePdfFallback({ threadId: 'thread-1', path: '/home/user/workspace/book.xlsx' })

  assert.match(calls[0].url, /\/api\/workspace\/knowledge\/file\?/)
  assert.match(calls[0].url, /office_render=pdf/)
  assert.match(calls[0].url, /kb_id=kb-1/)
  assert.match(calls[1].url, /\/api\/workspace\/file\?/)
  assert.match(calls[1].url, /office_render=pdf/)
  assert.doesNotMatch(calls[1].url, /knowledge/)
  assert.match(calls[2].url, /\/api\/viewer\/filesystem\/file\?/)
  assert.match(calls[2].url, /office_render=pdf/)
  assert.match(calls[3].url, /\/api\/v1\/agents\/sessions\/thread-1\/artifacts\//)
  assert.match(calls[3].url, /preview=true/)
  assert.match(calls[3].url, /office_render=pdf/)
  for (const call of calls) {
    assert.equal(call.authorization, 'Bearer test-token')
    assert.doesNotMatch(call.url, /^https?:\/\/(?!127\.0\.0\.1|localhost)/)
  }
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
