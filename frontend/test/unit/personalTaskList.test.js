import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

test('助理任务隐藏已完成，保留进行中、待处理和失败，并提供轨迹入口', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  t.after(() => server.close())
  const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/PersonalTaskList.vue')
  const render = sessions => renderToString(createSSRApp(() => h(component, { sessions })))
  const sessions = [
    { session_id: 'done', title: '已结束的工作', turn_status: 'completed' },
    { session_id: 'run-done', run_status: 'completed' },
    { session_id: 'active', title: '正在整理', turn_status: 'running' },
    { session_id: 'waiting', title: '需要确认', turn_status: 'waiting', waiting_for: 'answer' },
    { session_id: 'queued', title: '新的请求', turn_status: 'completed', has_pending_input: true },
    { session_id: 'failed', title: '失败待检查', turn_status: 'failed' }
  ]
  const html = await render(sessions)
  assert.doesNotMatch(html, /data-session-id="(?:done|run-done)"/)
  for (const id of ['active', 'waiting', 'queued', 'failed']) assert.match(html, new RegExp(`data-session-id="${id}"`))
  assert.match(html, /查看任务轨迹：需要确认/)
  assert.match(html, /需要处理/)
  assert.match(html, /排队中/)
  assert.match(html, /失败/)
  assert.doesNotMatch(await render(sessions.slice(0, 2)), /aria-label="委派任务"/)
  const error = await renderToString(createSSRApp(() => h(component, { error: '刷新失败' })))
  assert.match(error, /role="alert"[^>]*>刷新失败/)
  assert.match(error, /刷新委派任务/)
})
