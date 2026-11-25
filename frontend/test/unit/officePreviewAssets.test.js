import assert from 'node:assert/strict'
import { readdir, readFile, rm, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'

const CDN_PATTERN = /cdn\.jsdelivr|unpkg\.com|cdnjs\.cloudflare|fonts\.googleapis|fonts\.gstatic/i
const frontendRoot = fileURLToPath(new URL('../..', import.meta.url))

const walk = async (directory) => {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...(await walk(path)))
    else if (entry.isFile()) files.push(path)
  }
  return files
}

test('Office 预览资源由构建产物同源提供，且不进入入口包', async () => {
  const outDir = join(tmpdir(), `yuxi-office-assets-${Date.now()}`)
  try {
    await build({
      root: frontendRoot,
      configFile: join(frontendRoot, 'vite.config.js'),
      logLevel: 'silent',
      build: { outDir, emptyOutDir: true }
    })
    const files = await walk(outDir)
    const relative = files.map((file) => file.slice(outDir.length + 1))
    const textFiles = files.filter((file) => /\.(js|mjs|css|html|json)$/.test(file))
    for (const file of textFiles) {
      const source = await readFile(file, 'utf8')
      assert.doesNotMatch(source, CDN_PATTERN, file)
    }

    const html = await readFile(join(outDir, 'index.html'), 'utf8')
    const entryMatch = html.match(/src="\/(assets\/index-[^"]+\.js)"/)
    assert.ok(entryMatch, '构建必须产出入口脚本')
    const entry = entryMatch[1]
    const entrySource = await readFile(join(outDir, entry), 'utf8')
    assert.doesNotMatch(html, /file-viewer-|vendor\/docx|vendor\/xlsx|vendor\/pptx/)
    assert.doesNotMatch(entrySource, /configuredFileViewerRenderers|@file-viewer\/renderer-|vendor\/docx/)
    const knowledgeList = relative.find((file) => /^assets\/KnowledgeBaseInfoView-.*\.js$/.test(file))
    assert.ok(knowledgeList, '知识库页面必须仍是独立 chunk')
    const knowledgeSource = await readFile(join(outDir, knowledgeList), 'utf8')
    assert.doesNotMatch(knowledgeSource, /configuredFileViewerRenderers|@file-viewer\/renderer-/)

    const rendererChunk = relative.find(
      (file) => file.endsWith('.js') && file.includes('_virtual_file-viewer-renderers')
    )
    assert.ok(rendererChunk, 'Office 渲染器必须留在异步 chunk')
    assert.notEqual(rendererChunk, entry)
    const rendererSource = await readFile(join(outDir, rendererChunk), 'utf8')
    assert.match(rendererSource, /configuredFileViewerRenderers/)

    for (const asset of [
      'vendor/docx/docx.worker.js',
      'vendor/docx/jszip.min.js',
      'vendor/pptx/pptx.worker.js',
      'vendor/xlsx/sheet.worker.js'
    ]) {
      const info = await stat(join(outDir, asset))
      assert.equal(info.isFile(), true, asset)
      assert.ok(info.size > 0, asset)
    }
    assert.equal(relative.some((file) => file.startsWith('vendor/pdf/')), false)
    assert.equal(relative.some((file) => file.includes('/cad/') || file.includes('libarchive')), false)
  } finally {
    await rm(outDir, { recursive: true, force: true })
  }
})
