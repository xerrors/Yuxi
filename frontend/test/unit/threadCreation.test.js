import assert from 'node:assert/strict'
import test from 'node:test'
import { message } from 'ant-design-vue'

import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'
import { createThreadForContext } from '../../src/modules/session/model/threadCreation.js'

test('首次发送在创建 Thread 前冻结附件、图片和配置，后来草稿不能并入旧提交', async (t) => {
  let resolveCreation, submitted
  const h = await mountSessionWorkspace(t, undefined, { send: async (threadId, draft) => { submitted = { threadId, draft } } })
  h.agentStore.agents = [{ agent_id: 'agent', can_run: true }]
  h.agentStore.selectedAgentId = 'agent'
  t.mock.method(h.agentStore, 'fetchAgentDetail', async () => ({ can_run: true }))
  t.mock.method(h.state.chatThreadsStore, 'createThread', () => new Promise(resolve => { resolveCreation = resolve }))
  h.state.userInput.value = '原始输入'
  h.state.draftFilesByThread.value.__draft__ = [{ id: 'original-file' }]
  h.state.selectedModelByThread.__draft__ = 'original:model'
  h.state.savedToolApprovalMode.value = 'default'
  const images = [{ imageUrl: 'original-image' }]
  const sending = h.state.handleSendMessage({ images })
  assert.equal(h.state.userInput.value, '', '移到底部前清空冻结文本，避免多行编辑器二次收缩')
  await Promise.resolve()
  h.state.draftFilesByThread.value.__draft__.push({ id: 'next-draft-file' })
  images.push({ imageUrl: 'next-draft-image' })
  h.state.selectedModelByThread.__draft__ = 'next:model'
  h.state.savedToolApprovalMode.value = 'always_trust'
  resolveCreation({ id: 'created-thread', agent: { id: 'agent' }, yuxi: {} })
  await sending
  assert.equal(submitted.threadId, 'created-thread')
  assert.deepEqual(submitted.draft.attachments, [{ id: 'original-file' }])
  assert.deepEqual(submitted.draft.images, [{ imageUrl: 'original-image' }])
  assert.equal(submitted.draft.modelSpec, 'original:model')
  assert.equal(submitted.draft.toolApprovalMode, 'default')
})

test('延迟创建响应遇到上下文切换时不被接受', async () => {
  const context = { agentId: 'agent-a', projectId: 'auto', threadId: null }
  let currentContext = { ...context }
  let resolveRequest
  const response = new Promise((resolve) => {
    resolveRequest = resolve
  })

  const pending = createThreadForContext({
    context,
    getCurrentContext: () => currentContext,
    requestId: 'stable-request',
    create: async (requestId) => {
      assert.equal(requestId, 'stable-request')
      return response
    }
  })

  currentContext = { ...context, agentId: 'agent-b' }
  resolveRequest({ id: 'thread-a' })

  assert.deepEqual(await pending, { thread: { id: 'thread-a' }, accepted: false })
})

test('首条发送创建失败或未获得线程时恢复冻结文本和图片，不覆盖新编辑', async (t) => {
  for (const rejects of [true, false]) {
    await t.test(rejects ? '创建抛错' : '创建未返回线程', async (subtest) => {
      let restoredImages, errors = 0
      const h = await mountSessionWorkspace(subtest)
      h.agentStore.agents = [{ agent_id: 'agent', can_run: true }]
      h.agentStore.selectedAgentId = 'agent'
      h.state.userInput.value = '原始消息\n第二行'
      h.state.agentInputAreaRef.value = { restoreImages: images => { restoredImages = images } }
      subtest.mock.method(message, 'error', () => { errors++ })
      subtest.mock.method(h.state.chatThreadsStore, 'createThread', async () => {
        assert.equal(h.state.userInput.value, '')
        h.state.userInput.value = '后来编辑'
        if (rejects) throw new Error('creation failed')
        return null
      })
      await h.state.handleSendMessage({ images: [{ imageUrl: 'original-image' }] })
      assert.equal(h.state.userInput.value, '原始消息\n第二行\n后来编辑')
      assert.deepEqual(restoredImages, [{ imageUrl: 'original-image' }])
      assert.equal(errors, 1)
    })
  }
})

for (const uploadTarget of ['__draft__', 'thread-a']) {
  test(`创建响应丢失后重放审批模式，附件目标 ${uploadTarget} 按归属迁移`, async (t) => {
    const calls = []
    const h = await mountSessionWorkspace(t)
    h.agentStore.agents = [{ agent_id: 'agent', can_run: true }]
    h.agentStore.selectedAgentId = 'agent'
    h.state.attachmentUploadThreadKey.value = uploadTarget
    const detail = t.mock.method(h.agentStore, 'fetchAgentDetail', async () => ({ can_run: true }))
    t.mock.method(h.state.chatThreadsStore, 'createThread', async (...args) => {
      calls.push(JSON.parse(JSON.stringify(args)))
      if (calls.length === 1) throw new Error('response lost')
      return { id: 'same-session', agent: { id: 'agent' }, yuxi: {} }
    })
    h.state.savedToolApprovalMode.value = 'default'
    await assert.rejects(h.state.createActiveThread(), /response lost/)
    h.state.savedToolApprovalMode.value = 'always_trust'
    assert.equal(await h.state.createActiveThread(), 'same-session')
    assert.deepEqual(calls[1], calls[0])
    assert.equal(calls[1][1], null)
    assert.equal(calls[1][2].tool_approval_mode, 'default')
    assert.equal(detail.mock.calls.at(-1).arguments[0], 'agent')
    assert.equal(h.state.sessionAgentStatus.value, 'ready')
    assert.equal(h.state.threadCreationRequest.value, null)
    assert.equal(h.state.attachmentUploadThreadKey.value, uploadTarget === '__draft__' ? 'same-session' : uploadTarget)
  })
}

test('首次创建会话明确提交完全信任，不继承智能体的审批缺省值', async (t) => {
  const h = await mountSessionWorkspace(t)
  h.agentStore.agents = [{ agent_id: 'agent', can_run: true, config_json: { context: { tool_approval_mode: 'default' } } }]
  h.agentStore.selectedAgentId = 'agent'
  t.mock.method(h.agentStore, 'fetchAgentDetail', async () => ({ can_run: true }))
  t.mock.method(h.state.chatThreadsStore, 'createThread', async (_agent, _project, config) => {
    assert.equal(config.tool_approval_mode, 'always_trust')
    return { id: 'new-session', agent: { id: 'agent' }, yuxi: config }
  })
  assert.equal(await h.state.createActiveThread(), 'new-session')
  assert.equal(h.state.currentToolApprovalMode.value, 'always_trust')
})

test('新建会话按稳定 agent_id 找到当前智能体，详情到达后替换列表摘要', async (t) => {
  const h = await mountSessionWorkspace(t)
  h.agentStore.selectedAgentId = 'slug'
  h.agentStore.agents = [{ agent_id: 'slug', name: '摘要' }]
  assert.equal(h.state.currentAgent.value.name, '摘要')
  h.agentStore.agentDetails.slug = { agent_id: 'slug', name: '完整详情' }
  assert.equal(h.state.currentAgent.value.name, '完整详情')
  h.agentStore.selectedAgentId = 'missing'
  assert.equal(h.state.currentAgent.value, null)
})

test('模型选择按当前选择、Session、智能体默认值响应式回退，新建草稿独立', async (t) => {
  const h = await mountSessionWorkspace(t, undefined, { threadId: 'thread' })
  h.state.selectedModelByThread.thread = 'selected:model'
  h.state.selectedModelByThread.__draft__ = 'draft:model'
  h.runtime.getThreadState('thread').detail = { id: 'thread', agent: { model: 'session:model' } }
  h.state.configStore.config.default_model = 'agent:model'
  assert.equal(h.state.currentModelSpec.value, 'selected:model')
  delete h.state.selectedModelByThread.thread
  assert.equal(h.state.currentModelSpec.value, 'session:model')
  h.runtime.getThreadState('thread').detail = null
  assert.equal(h.state.currentModelSpec.value, 'agent:model')
  h.state.currentChatId.value = null
  assert.equal(h.state.currentModelSpec.value, 'draft:model')
})

test('预写 Thread 身份与已有历史都委派阅读器就绪，页面不重建读取流程', async (t) => {
  for (const historyLoaded of [false, true]) {
    await t.test(historyLoaded ? '历史已加载' : '历史未加载', async (subtest) => {
      const reads = []
      const h = await mountSessionWorkspace(subtest, () => assert.fail('页面不能直接读取历史'), {
        threadId: 'thread', runtime: {
          resumeActiveRunForThread: () => assert.fail('页面不能直接恢复读取订阅'),
          resumeQueuedInputs: async () => {}
        }
      })
      h.state.chatThreadsStore.threads = [{ id: 'thread', agent: { id: 'agent' }, yuxi: {} }]
      h.runtime.getThreadState('thread').historyLoaded = historyLoaded
      subtest.mock.method(h.agentStore, 'fetchAgentDetail', async () => ({ can_run: historyLoaded }))
      subtest.mock.method(h.state.chatThreadsStore, 'markThreadViewed', () => {})
      h.state.timelineRef.value = { refresh: async () => { reads.push(h.state.currentChatId.value); return true } }
      assert.equal(await h.state.selectThreadFromRoute('thread'), true)
      assert.deepEqual(reads, ['thread'])
    })
  }
})

test('附件上传固定打开时的目标，切换会话和再次打开分别保留各自归属', async (t) => {
  const h = await mountSessionWorkspace(t, undefined, { threadId: 'thread-a' })
  h.agentStore.selectedAgentId = 'agent'
  await h.state.handleAttachmentUpload()
  h.state.currentChatId.value = 'thread-b'
  h.runtime.getThreadState('thread-b').detail = { id: 'thread-b', agent: { id: 'agent' }, yuxi: {} }
  await h.state.handleAttachmentUpload()
  h.state.handleTmpAttachmentsAdded([{ id: 'file-a' }])
  assert.deepEqual(h.state.draftFilesByThread.value, { 'thread-a': [{ id: 'file-a' }] })
  h.state.attachmentUploadModalOpen.value = false
  await h.state.handleAttachmentUpload()
  h.state.handleTmpAttachmentsAdded([{ id: 'file-b' }])
  assert.deepEqual(h.state.draftFilesByThread.value['thread-b'], [{ id: 'file-b' }])
  assert.deepEqual(h.state.draftFilesByThread.value['thread-a'], [{ id: 'file-a' }])
})

for (const status of [403, 404, 503]) {
  test(`历史先选中，智能体详情 ${status} 只影响输入可用性`, async (t) => {
    const history = [], execution = []
    const detail = Promise.withResolvers()
    const h = await mountSessionWorkspace(t, () => assert.fail('工作区应通过阅读器读取历史'), {
      threadId: 'previous', runtime: {
        fetchAgentState: () => execution.push('state'),
        resumeActiveRunForThread: () => execution.push('resume'),
        resumeQueuedInputs: () => execution.push('queue')
      }
    })
    h.runtime.getThreadState('history').detail = { id: 'history', agent: { id: 'agent' }, yuxi: {} }
    const toast = t.mock.method(message, 'error', () => {})
    t.mock.method(h.agentStore, 'fetchAgentDetail', () => detail.promise)
    t.mock.method(h.state.chatThreadsStore, 'markThreadViewed', () => {})
    let preparation
    h.state.timelineRef.value = { refresh: async () => {
      const threadId = h.state.currentChatId.value
      history.push(threadId)
      h.runtime.getThreadState(threadId).historyLoaded = true
      preparation = h.state.initializeThreadInput({ threadId })
      return true
    } }
    assert.equal(await h.state.selectThreadFromRoute('history'), true)
    assert.deepEqual(history, ['history'])
    assert.equal(h.state.currentChatId.value, 'history')
    assert.equal(h.state.isSessionLoading.value, false)
    assert.equal(h.state.sessionAgentStatus.value, 'loading')
    assert.equal(h.state.agentExecutionAvailable.value, false)

    detail.reject(Object.assign(new Error('agent unavailable'), { status }))
    await preparation

    assert.equal(h.state.currentChatId.value, 'history')
    assert.equal(h.state.isSessionLoading.value, false)
    assert.equal(h.state.agentExecutionAvailable.value, false)
    assert.deepEqual(execution, [])
    assert.equal(toast.mock.callCount(), 0, '详情不可用不能重复提示错误')
    assert.equal(h.state.sessionAgentStatus.value, status === 503 ? 'error' : 'unavailable')
  })
}

for (const status of [403, 404, 410, 503, undefined]) {
  test(`恢复未缓存线程遇到 ${status ?? '网络错误'}，只有确定失效才拒绝路由`, async (t) => {
    const reads = []
    const h = await mountSessionWorkspace(t)
    t.mock.method(message, 'error', () => {})
    t.mock.method(h.agentApi, 'getPublicThread', async () => { throw { status } })
    h.state.timelineRef.value = { refresh: async () => { reads.push(h.state.currentChatId.value); throw { status } } }
    t.mock.method(h.state.chatThreadsStore, 'updateThreadSummary', () => assert.fail('失败详情不能写入列表'))
    assert.equal(await h.state.selectThreadFromRoute('remembered-thread'), [403, 404, 410].includes(status) ? false : null)
    assert.deepEqual(reads, ['remembered-thread'])
  })
}

for (const historyLoaded of [false, true]) {
  for (const status of [403, 404, 410, 503, undefined]) {
    test(`缓存线程（历史${historyLoaded ? '已加载' : '未加载'}）遇到 ${status ?? '网络错误'}，失效结果传回路由协调器`, async (t) => {
      const reads = []
      const h = await mountSessionWorkspace(t, async () => { throw { status } }, {
        threadId: historyLoaded ? 'cached' : 'previous',
        runtime: { resumeActiveRunForThread: () => assert.fail('失效或读取失败时不能恢复执行') }
      })
      h.state.chatThreadsStore.threads = [{ id: 'cached', agent: { id: 'agent' }, yuxi: {} }]
      h.runtime.getThreadState('cached').historyLoaded = historyLoaded
      t.mock.method(h.agentStore, 'fetchAgentDetail', async () => ({ can_run: false }))
      h.state.timelineRef.value = { refresh: async () => { reads.push(h.state.currentChatId.value); throw { status } } }
      t.mock.method(h.state.chatThreadsStore, 'updateThreadSummary', () => assert.fail('错误响应不能写入导航列表'))
      t.mock.method(message, 'error', () => {})
      assert.equal(await h.state.selectThreadFromRoute('cached'), [403, 404, 410].includes(status) ? false : null)
      assert.deepEqual(reads, ['cached'])
      assert.equal(h.state.isLoadingMessages.value, false)
    })
  }
}
