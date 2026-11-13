import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const rendererSource = readFileSync(
  new URL('../../src/modules/session/ui/tools/toolRenderers.js', import.meta.url),
  'utf8'
)
const indexSource = readFileSync(
  new URL('../../src/modules/session/ui/tools/index.js', import.meta.url),
  'utf8'
)
const errorSource = readFileSync(
  new URL('../../src/modules/session/ui/tools/ToolRendererUnavailable.vue', import.meta.url),
  'utf8'
)

test('工具 renderer 使用异步组件并提供局部加载失败卡片', () => {
  assert.match(rendererSource, /defineAsyncComponent/)
  assert.match(rendererSource, /import\('\.\/renderers\/WebSearchTool\.vue'\)/)
  assert.match(rendererSource, /errorComponent: ToolRendererUnavailable/)
  assert.doesNotMatch(rendererSource, /import WebSearchTool from '\.\/renderers\/WebSearchTool\.vue'/)
  assert.match(errorSource, /role="status"/)
})

test('工具目录不再静态重导出低频 renderer', () => {
  assert.match(indexSource, /ToolCallRenderer\.vue/)
  assert.doesNotMatch(indexSource, /renderers\/.*\.vue/)
})
