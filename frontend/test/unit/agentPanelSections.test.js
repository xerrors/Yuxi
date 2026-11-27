import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  FILE_TREE_SECTION,
  START_SECTION,
  WORKSPACE_TREE_SECTION,
  closeAgentPanelSection,
  shouldPollAgentPanelFilesystem,
  upsertAgentPanelSection
} from '../../src/modules/session/model/agentPanelSections.js'
import { normalizePreviewResponse } from '../../src/shared/lib/file_preview.js'

test('同一子线程重复打开时更新已有 Section 而不新增', () => {
  const section = { key: 'session:thread-1', type: 'session', threadId: 'thread-1', title: '研究员' }
  const first = upsertAgentPanelSection([FILE_TREE_SECTION], section)
  const second = upsertAgentPanelSection(first, { ...section, title: '研究助手' })
  assert.equal(second.length, 2)
  assert.equal(second[1].title, '研究助手')
})

test('关闭活动 Tab 后激活相邻项', () => {
  const sections = [
    FILE_TREE_SECTION,
    { key: 'session:a', type: 'session', threadId: 'a' },
    { key: 'session:b', type: 'session', threadId: 'b' }
  ]
  assert.deepEqual(closeAgentPanelSection(sections, 'session:a', 'session:a'), {
    sections: [sections[0], sections[2]],
    activeKey: 'session:b'
  })
})

test('关闭最后一个文件标签后回到唯一的开始页', () => {
  assert.deepEqual(closeAgentPanelSection([FILE_TREE_SECTION], FILE_TREE_SECTION.key, FILE_TREE_SECTION.key), {
    sections: [START_SECTION],
    activeKey: START_SECTION.key
  })
  assert.deepEqual(upsertAgentPanelSection([START_SECTION], START_SECTION), [START_SECTION])
})

test('normalizePreviewResponse 正确解析 JSON 预览结构而不把 raw payload 当纯文本', async () => {
  const jsonResponse = new Response(
    JSON.stringify({
      content: 'def bubble_sort():\n    pass\n',
      preview_type: 'text',
      supported: true,
      message: null
    }),
    {
      headers: { 'content-type': 'application/json' }
    }
  )

  const parsed = await normalizePreviewResponse(jsonResponse, {
    path: '/outputs/bubble_sort.py',
    loading: true
  })

  assert.equal(parsed.content, 'def bubble_sort():\n    pass\n')
  assert.equal(parsed.previewType, 'text')
  assert.equal(parsed.supported, true)
  assert.equal(parsed.loading, false)
  assert.equal(parsed.status, 'ready')
})

test('normalizePreviewResponse 解包 JSON artifact 的统一预览结构', async () => {
  const jsonResponse = new Response(
    JSON.stringify({
      content: '{"result":42}',
      preview_type: 'text',
      supported: true
    }),
    {
      headers: { 'content-type': 'application/json' }
    }
  )

  const parsed = await normalizePreviewResponse(jsonResponse, {
    path: '/home/user/workspace/projects/demo/outputs/result.json'
  })

  assert.equal(parsed.content, '{"result":42}')
  assert.equal(parsed.previewType, 'text')
  assert.equal(parsed.supported, true)
  assert.equal(parsed.status, 'ready')
})

test('normalizePreviewResponse 不支持格式时标记 status 为 unsupported', async () => {
  const binaryResponse = new Response(new Uint8Array([0, 1, 2, 3]), {
    headers: { 'content-type': 'application/octet-stream' }
  })
  globalThis.URL = globalThis.URL || {}
  globalThis.URL.createObjectURL = () => 'blob:mock-url'

  const parsed = await normalizePreviewResponse(binaryResponse, {
    path: '/outputs/data.bin'
  })

  assert.equal(parsed.status, 'unsupported')
  assert.equal(parsed.supported, false)
})

test('暂停队列仍有请求时禁用上下文压缩', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/SessionWorkspace.vue', import.meta.url),
    'utf8'
  )
  const position = source.indexOf('<ContextUsagePanel')
  const action = source.slice(position, source.indexOf('/>', position))
  const panel = readFileSync(new URL('../../src/modules/session/ui/ContextUsagePanel.vue', import.meta.url), 'utf8')
  const threadView = readFileSync(
    new URL('../../src/modules/session/ui/SessionThreadView.vue', import.meta.url),
    'utf8'
  )
  const handler = threadView.slice(
    threadView.indexOf('const handleContextCompression'),
    threadView.indexOf('const handleSendOrStop')
  )

  assert.match(action, /:compression-disabled="mainThread\?\.compressionDisabled \?\? true"/)
  assert.match(panel, /:disabled="compressing \|\| compressionDisabled"/)
  const disabled = threadView.slice(threadView.indexOf('compressionDisabled:'), threadView.indexOf('compressContext:'))
  assert.match(disabled, /hasQueuedInputs\.value/)
  assert.match(handler, /hasQueuedInputs\.value/)
})

test('文件树仅在页面可见的运行期文件视图中轮询', () => {
  const base = {
    panelOpen: true,
    pageVisible: true,
    streaming: true,
    activeSection: FILE_TREE_SECTION,
    activePreview: null
  }

  assert.equal(shouldPollAgentPanelFilesystem(base), true)
  assert.equal(shouldPollAgentPanelFilesystem({ ...base, activeSection: WORKSPACE_TREE_SECTION }), false)
  assert.equal(shouldPollAgentPanelFilesystem({ ...base, panelOpen: false }), false)
  assert.equal(shouldPollAgentPanelFilesystem({ ...base, pageVisible: false }), false)
  assert.equal(shouldPollAgentPanelFilesystem({ ...base, streaming: false }), false)
  assert.equal(
    shouldPollAgentPanelFilesystem({
      ...base,
      activeSection: { type: 'file' },
      activePreview: {
        path: '/home/user/workspace/projects/abc/outputs/report.md',
        workdir: true
      }
    }),
    true
  )
  assert.equal(
    shouldPollAgentPanelFilesystem({
      ...base,
      activeSection: { type: 'file' },
      activePreview: { path: '/home/user/workspace/saved_artifacts/report.md', workdir: false }
    }),
    false
  )
})
