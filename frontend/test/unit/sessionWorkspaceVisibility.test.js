import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'

test('人工等待取消期间显示停止反馈，即使没有流和排队消息', async (t) => {
  const { state, runtime } = await mountSessionWorkspace(t, undefined, { threadId: 'thread' })
  const thread = runtime.getThreadState('thread')
  thread.cancellationPending = true
  thread.turnStatus = 'requires_action'
  assert.equal(state.isProcessing.value, true)
  assert.equal(state.isReplyLoading.value, true)
  assert.equal(state.replyLoadingText.value, '正在停止，等待任务清理完成...')
})

test('隐藏成员标签继续接收终态但不标已读；显示后恢复可见副作用', async (t) => {
  const resumed = []
  const { state, props } = await mountSessionWorkspace(t, undefined, {
    threadId: 'child', props: { visible: false }, runtime: { resumeQueuedInputs: (id) => resumed.push(id) }
  })
  const viewed = []
  t.mock.method(state.chatThreadsStore, 'markThreadViewed', (id) => { viewed.push(id) })
  const complete = () => {
    state.runtimeView.onTerminalDetected({ threadId: 'child', runId: 'run' })
  }
  complete()
  assert.deepEqual(resumed, [], '恢复由共享运行内核负责')
  assert.deepEqual(viewed, [])
  props.visible = true
  complete()
  assert.deepEqual(viewed, ['child'])
  state.pageVisible.value = false
  complete()
  assert.deepEqual(viewed, ['child'])
  state.pageVisible.value = true
  state.workspaceActivated.value = false
  complete()
  assert.deepEqual(viewed, ['child'])
  assert.equal(state.agentPanelFilesystemRefreshVersion.value, 4)
  state.runtimeView.onTerminalDetected({ threadId: 'other-thread', runId: 'other-run' })
  assert.equal(state.agentPanelFilesystemRefreshVersion.value, 4)
})

test('另一视图完成审批后，本视图立即解除旧等待点的输入遮罩', async (t) => {
  const { state, runtime } = await mountSessionWorkspace(t, undefined, { threadId: 'thread' })
  const shared = runtime.getThreadState('thread')
  const pending = { threadId: 'thread', kind: 'question', interruptedRunId: 'run', waitpointId: 'approval-1', questions: [{ question_id: 'q', question: '问题' }] }
  shared.pendingInterrupt = pending
  state.restorePendingInterruptForThread('thread')
  assert.equal(state.currentApprovalModalVisible.value, true)
  shared.pendingInterrupt = null
  assert.equal(state.currentApprovalModalVisible.value, false)
  shared.pendingInterrupt = { ...pending, interruptedRunId: 'run2', waitpointId: 'approval-2' }
  assert.equal(state.currentApprovalModalVisible.value, false)
  state.restorePendingInterruptForThread('thread')
  assert.equal(state.currentApprovalModalVisible.value, true)
})

const view = readFileSync(new URL('../../src/pages/AgentView.vue', import.meta.url), 'utf8')
/** 渲染真实会话模板，子面板由组件边界替身隔离。 */
async function renderWorkspace(component, state) {
  const app = createSSRApp({ ...component, setup: () => state })
  app.config.warnHandler = () => {}
  app.component('a-spin', { setup: () => () => h('span', { 'data-shared-spin': '' }) })
  return renderToString(app)
}

test('提交进行中不显示未知接收结果，失败后才提供幂等重试入口', async (t) => {
  const { state, component } = await mountSessionWorkspace(t, undefined, { threadId: 'thread', stubChildren: true })
  for (const submitting of [true, false]) {
    state.pendingSends.value.thread = { submitting }
    const html = await renderWorkspace(component, state)
    assert.equal(html.includes('发送结果待确认'), !submitting)
  }
})

test('智能体加载或不可用时保留编辑器区域，并禁止输入', async (t) => {
  const { state, agentStore, component } = await mountSessionWorkspace(t, undefined, { threadId: 'thread', stubChildren: true })
  let status = 'loading'
  t.mock.method(agentStore, 'fetchAgentDetail', () => {
    if (status === 'loading') return new Promise(() => {})
    if (status === 'error') return Promise.reject(new Error('offline'))
    return Promise.resolve({ can_run: status === 'ready' })
  })
  for (status of ['loading', 'ready', 'unavailable', 'error']) {
    const refresh = state.refreshSessionAgent('thread', 'agent')
    if (status !== 'loading') await refresh
    const html = await renderWorkspace(component, state)
    assert.ok(html.includes('message-input-surface'), `${status} 不能卸载编辑器`)
    const input = html.match(/<input[^>]*data-session-input[^>]*>/)?.[0]
    assert.ok(input, `${status} 必须保留输入组件`)
    assert.equal(input.includes(' disabled'), status !== 'ready')
  }
})

test('新建路由且没有线程才显示欢迎布局，加载和空消息不改变布局', async (t) => {
  assert.ok(view.includes(':is-new-session="!threadId"'), '新建布局由路由传入')
  const { state, props, runtime, component } = await mountSessionWorkspace(t, undefined, { stubChildren: true })
  for (const isNewSession of [false, true]) {
    for (const threadId of [null, 'created-thread']) {
      props.isNewSession = isNewSession
      state.currentThreadId.value = threadId
      for (const isLoadingMessages of [false, true]) {
        if (threadId) runtime.getThreadState(threadId).readingLoading = isLoadingMessages
        for (const runs of [[], [{ id: 'history' }]]) {
          runtime.threadRuns['created-thread'] = runs
          const html = await renderWorkspace(component, state)
          assert.equal(html.includes('start-screen'), isNewSession && !threadId)
          assert.equal(html.includes(state.randomGreeting), isNewSession && !threadId)
          assert.equal(html.includes('正在加载消息'), false, '编辑器上方不再单独加载消息')
        }
      }
    }
  }
  props.isNewSession = true
  state.currentThreadId.value = null
  assert.equal(state.showStartScreen.value, true)
  state.currentThreadId.value = 'just-created'
  assert.equal(state.showStartScreen.value, false, '创建完成立即收起欢迎布局')
  props.isNewSession = false
  state.currentThreadId.value = null
  assert.equal(state.showStartScreen.value, false, '已有会话加载时不显示欢迎布局')
})

test('首条待消费输入只展示消息气泡，后续输入、暂停及附件准备保留队列入口', async (t) => {
  const { state, runtime } = await mountSessionWorkspace(t, undefined, { threadId: 'thread' })
  const thread = runtime.getThreadState('thread')
  Object.assign(thread, {
    pendingInputId: 'primary', optimisticInputs: { primary: [{ content: '首条消息' }] },
    queuedInputs: [{ input_id: 'primary' }, { input_id: 'later' }], queueSnapshot: { status: 'ready' }
  })
  assert.equal(state.primaryQueuedInput.value.input_id, 'primary')
  assert.deepEqual(state.visibleQueuedInputs.value.map(input => input.input_id), ['later'])
  assert.equal(state.currentQueuedInputs.value.length, 2, '展示去重不能改写持久队列投影')
  thread.queueSnapshot.status = 'paused'
  assert.equal(state.primaryQueuedInput.value, null)
  assert.equal(state.visibleQueuedInputs.value.length, 2, '暂停后必须保留继续队列及取消入口')
  thread.queueSnapshot.status = 'ready'
  thread.queuedInputs[0].attachment_status = 'preparing'
  assert.equal(state.primaryQueuedInput.value, null)
  assert.equal(state.visibleQueuedInputs.value.length, 2, '附件准备状态仍需展示')
  delete thread.queuedInputs[0].attachment_status
  delete thread.optimisticInputs.primary
  assert.equal(state.primaryQueuedInput.value, null)
  assert.equal(state.visibleQueuedInputs.value.length, 2, '没有消息气泡时不能隐藏排队输入')
})

test('首条输入在回复位置保留取消入口，取消期间禁用重复操作', async (t) => {
  const { state, runtime, component } = await mountSessionWorkspace(t, undefined, { threadId: 'thread', stubChildren: true })
  const thread = runtime.getThreadState('thread')
  for (const primary of [false, true]) {
    for (const cancelling of [false, true]) {
      for (const activeRunId of [null, 'running']) {
        Object.assign(thread, {
          pendingInputId: 'pending', optimisticInputs: { pending: [{ content: '输入' }] },
          queuedInputs: primary ? [{ input_id: 'pending' }] : [],
          queueSnapshot: { status: 'ready' }, historyLoaded: true, activeRunId
        })
        state.cancellingInputIds.clear()
        if (cancelling) state.cancellingInputIds.add('pending')
        const html = await renderWorkspace(component, state)
        const button = html.match(/<button[^>]*aria-label="取消这条待处理输入"[^>]*>/)?.[0]
        const visible = primary && !activeRunId
        assert.equal(Boolean(button), visible, 'Run 已启动时使用停止运行入口')
        assert.equal(Boolean(button?.includes(' disabled')), visible && cancelling)
      }
    }
  }
})

test('仅有 warnings 时状态面板展示警告，警告清除后恢复空态', async (t) => {
  const { state, runtime } = await mountSessionWorkspace(t, undefined, { threadId: 'thread' })
  const thread = runtime.getThreadState('thread')
  thread.agentState = { warnings: [
    { code: 'mcp_unavailable', source: 'mcp:attendance', message: '考勤未连接' },
    { code: 'mcp_unavailable', source: 'mcp:github', message: 'GitHub 未连接' }
  ] }
  assert.equal(state.hasVisibleStateSections.value, true)
  thread.agentState = { warnings: [] }
  assert.equal(state.hasVisibleStateSections.value, false)
})

test('状态警告逐条渲染安全文本，空列表不显示警告区域', async (t) => {
  const { state, runtime, component } = await mountSessionWorkspace(t, undefined, { threadId: 'thread', stubChildren: true })
  const thread = runtime.getThreadState('thread')
  const renderState = async (warnings) => {
    thread.agentState = { warnings }
    const app = createSSRApp({ ...component, setup: () => state })
    app.config.warnHandler = () => {}
    return renderToString(app)
  }
  const html = await renderState([
    { code: 'mcp_unavailable', source: 'mcp:attendance', message: '考勤未连接' },
    { code: 'mcp_unavailable', source: 'mcp:github', message: 'GitHub <连接失败>' }
  ])
  const warningSection = html.match(/<section[^>]*class="state-warning"[\s\S]*?<\/section>/)?.[0] || ''
  assert.equal((warningSection.match(/<p[^>]*>/g) || []).length, 2)
  assert.ok(html.includes('考勤未连接'))
  assert.ok(html.includes('GitHub &lt;连接失败&gt;'))
  assert.ok(!(await renderState([])).includes('state-warning'))
})
