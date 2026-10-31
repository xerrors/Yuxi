import assert from 'node:assert/strict'
import test from 'node:test'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'
import { personalMessages } from '../../src/modules/session/model/personalMessages.js'

test('个人助理不改写普通对话的导航目标', async (t) => {
  const h = await mountSessionWorkspace(t, undefined, { stubChildren: true, props: { isNewSession: false, personalMode: true } })
  h.state.chatThreadsStore.setCurrentThreadId('ordinary')
  h.state.setCurrentThreadId('assistant')
  assert.equal(h.state.chatThreadsStore.currentThreadId, 'ordinary')
})

test('助理侧栏不包含文件树，关闭最后轨迹后收起且不回到文件页', async (t) => {
  const h = await mountSessionWorkspace(t, undefined, { stubChildren: true, props: { isNewSession: false, personalMode: true } })
  assert.deepEqual(h.state.agentPanelSections.value, [])
  h.state.agentPanelSections.value = [{ key: 'session:task', type: 'session', sessionId: 'task' }]
  h.state.agentPanelActiveSectionKey.value = 'session:task'
  h.state.isFilePanelOpen.value = true
  h.state.closeAgentPanelSection('session:task')
  assert.equal(h.state.isFilePanelOpen.value, false)
  assert.equal(h.state.agentPanelActiveSectionKey.value, '')
  h.state.resetAgentPanelState()
  assert.deepEqual(h.state.agentPanelSections.value, [])
})

test('普通对话侧栏继续提供文件树', async (t) => {
  const h = await mountSessionWorkspace(t, undefined, { stubChildren: true })
  assert.deepEqual(h.state.agentPanelSections.value.map(section => section.key), ['file-tree'])
  h.state.resetAgentPanelState()
  assert.deepEqual(h.state.agentPanelSections.value.map(section => section.key), ['file-tree'])
})

test('资料卡片按实际主对话宽度平铺，窄屏、关闭和最大化轨迹不预留列', async (t) => {
  const h = await mountSessionWorkspace(t, undefined, { stubChildren: true,
    props: { isNewSession: false, personalMode: true, personalProfileOpen: true } })
  h.state.localUIState.chatMainVisibleWidth = 1100
  assert.equal(h.state.personalProfileEmbedded.value, true)
  h.state.localUIState.chatMainVisibleWidth = 1099
  assert.equal(h.state.personalProfileEmbedded.value, false)
  h.state.localUIState.chatMainVisibleWidth = 1210
  h.props.personalProfileOpen = false
  assert.equal(h.state.personalProfileEmbedded.value, false)
  h.props.personalProfileOpen = true
  h.state.isAgentPanelMaximized.value = true
  assert.equal(h.state.personalProfileEmbedded.value, false)
  h.state.isAgentPanelMaximized.value = false
  h.props.personalMode = false
  assert.equal(h.state.personalProfileEmbedded.value, false)
})

test('只有成功 send_to_user 的结果显示为消息，content、失败、参数流和静默执行不泄露', () => {
  const input = { id: 'user', type: 'human', content: '你好' }
  const raw = { id: 'ai', type: 'ai', content: '内部正文', reasoning_content: '内部推理', tool_calls: [
    { id: 'pending', name: 'send_to_user', status: 'pending', args: { content: '未发送' } },
    { id: 'failed', name: 'send_to_user', status: 'error', tool_call_result: { content: '{"content":"失败内容"}' } },
    { id: 'sent', name: 'send_to_user', status: 'success', tool_call_result: { content: '{"content":"真正发给用户"}' } },
    { id: 'other', name: 'web_search', status: 'success', tool_call_result: { content: '{"content":"搜索资料"}' } }
  ] }
  const projected = personalMessages([input, raw])
  assert.equal(projected.length, 2)
  assert.equal(projected[0], input)
  assert.equal(projected[1].content, '真正发给用户')
  assert.equal(projected[1].reasoning_content, '')
  assert.deepEqual(projected[1].tool_calls, [])
  assert.deepEqual(personalMessages([raw, { ...raw, id: 'silent', tool_calls: [] }]), [projected[1]])
  assert.deepEqual(personalMessages([input, raw]), projected, '刷新回读与实时投影使用相同稳定身份')
})

test('创建会话成功后投影稳定入口，失败、等待与缺失身份不生成气泡', async (t) => {
  const call = { id: 'created', name: 'create_session', status: 'success',
    tool_call_result: { content: JSON.stringify({ session_id: 'task-session', name: '整理资料' }) } }
  const source = { id: 'ai-create', run_id: 'run', type: 'ai', content: '内部正文',
    reasoning_content: '内部推理', tool_calls: [call,
      { ...call, id: 'failed', status: 'error' },
      { ...call, id: 'waiting', status: 'waiting' },
      { ...call, id: 'business-error', tool_call_result: { content: '{"status":"failed","session_id":"not-created"}' } },
      { ...call, id: 'missing', tool_call_result: { content: '{}' } },
      { ...call, id: 'empty', tool_call_result: { content: '{"session_id":""}' } },
      { ...call, id: 'blank', tool_call_result: { content: '{"session_id":"   "}' } },
      { ...call, id: 'sent', name: 'send_to_user', tool_call_result: { content: '{"content":"任务已提交"}' } }
    ] }
  const projected = personalMessages([source])
  assert.equal(projected.length, 2)
  assert.equal(projected[0].type, 'created-session')
  assert.equal(projected[0].id, 'ai-create:session:created')
  assert.deepEqual(projected[0].session, { id: 'task-session', name: '整理资料' })
  assert.equal(projected[0].content, '')
  assert.equal(projected[0].reasoning_content, '')
  assert.deepEqual(projected[0].tool_calls, [])
  assert.equal(projected[1].content, '任务已提交')
  assert.deepEqual(personalMessages([structuredClone(source)]), projected, '刷新保持入口身份及调用顺序')

  const { createTestServer } = await import('../helpers/createTestServer.js')
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  t.after(() => server.close())
  const { getMessageGroupDisplayItems } = await server.ssrLoadModule('/src/modules/session/model/messageGrouping.js')
  const items = getMessageGroupDisplayItems({ messages: projected })
  assert.deepEqual(items.map(item => item.message.id), projected.map(message => message.id))
})

test('助理发送固定为 steer，保留图片附件，不暴露停止', async (t) => {
  const sent = []
  const h = await mountSessionWorkspace(t, undefined, { threadId: 'thread', stubChildren: true,
    props: { isNewSession: false, personalMode: true }, send: async (thread, draft) => { sent.push({ thread, draft }); return true } })
  t.mock.method(h.agentStore, 'fetchAgentDetail', async () => ({ can_run: true }))
  await h.state.refreshSessionAgent('thread', 'agent')
  h.state.userInput.value = '你好'
  const image = { imageUrl: 'data:image/png;base64,AQ==', mimeType: 'image/png' }
  h.state.draftFilesByThread.value.thread = [{ id: 'file-a', filename: 'notes.txt', bytes: 12 }]
  await h.state.handleSendMessage({ images: [image] })
  assert.equal(sent[0].draft.mode, 'steer')
  assert.equal(sent[0].draft.modelSpec, null)
  assert.equal(sent[0].draft.toolApprovalMode, null)
  assert.deepEqual(sent[0].draft.images, [image])
  assert.equal(sent[0].draft.attachments[0].id, 'file-a')
  h.state.sendCooldownActive.value = false
  const state = h.state.getThreadState('thread')
  state.activeRunId = 'run'
  state.isStreaming = true
  state.activeRunSteerable = true
  h.state.userInput.value = ''
  assert.equal(h.state.shouldShowStopButton.value, false)
  assert.equal(h.state.isSendButtonDisabled.value, true)
  const cancel = t.mock.method(h.runtime, 'requestTurnCancellation', async () => { throw Error('不得停止助理') })
  await h.state.handleSendOrStop()
  assert.equal(cancel.mock.callCount(), 0)
  h.state.sendCooldownActive.value = false
  h.state.userInput.value = '换个方向'
  await h.state.handleSendMessage()
  assert.equal(sent.at(-1).draft.mode, 'steer')
})

test('只读轨迹保留完整普通聊天渲染，但不能从输入或 widget 提交消息', async (t) => {
  const sent = []
  const h = await mountSessionWorkspace(t, undefined, { threadId: 'thread', stubChildren: true,
    props: { isNewSession: false, readOnly: true, personalMode: true }, send: async (...args) => sent.push(args) })
  assert.equal(h.state.effectivePersonalMode.value, false)
  h.state.userInput.value = '不能发送'
  assert.equal(await h.state.handleSendMessage(), false)
  assert.equal(sent.length, 0)
})
