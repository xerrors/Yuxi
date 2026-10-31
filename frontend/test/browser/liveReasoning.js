// 在当前分支 Vite 页面执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/liveReasoning.js
// 公开 HTTP/SSE 边界使用合成数据，实际会话页面、runtime 与 DOM 保持真实装配，不执行模型请求。
// prettier-ignore
async page => {
  const origin = await page.evaluate(() => location.origin)
  const threadId = '00000000-0000-4000-8000-000000000091'
  const agentId = 'reasoning-fixture', turnId = 'reasoning-turn', runId = 'reasoning-run'
  const check = (value, message) => { if (!value) throw new Error(message) }
  const config = { model: 'replay:chat', tool_approval_mode: 'default', tools: [], skills: [] }
  const agent = { agent_id: agentId, slug: agentId, name: '流式展示验证', config_json: { context: config }, configurable_items: {}, metadata: {} }
  const item = (id, messageId, extra = {}) => ({ id, type: 'message', role: 'assistant', status: 'in_progress', turn_id: turnId,
    content: [], yuxi: { run_id: runId, message_id: messageId, output_index: messageId }, ...extra })
  const history = item('history', 1, { status: 'completed', turn_id: 'history-turn',
    content: [{ type: 'output_text', text: '历史回答保留' }],
    yuxi: { run_id: 'history-run', message_id: 1, reasoning: { 0: '历史思考原文' } } })
  const human = item('human', 2, { role: 'user', status: 'completed', content: [{ type: 'input_text', text: '检查流式思考的展示' }] })
  let completed = false, snapshot = [history, human], sequence = 0
  const run = () => ({ id: runId, turn_id: turnId, status: completed ? 'completed' : 'running' })
  const turn = () => ({ id: turnId, status: completed ? 'completed' : 'in_progress', yuxi: {
    current_run_id: runId, result_run_id: completed ? runId : null, runs: [run()], output: [] } })
  const thread = () => ({ id: threadId, object: 'agent.session', status: turn().status,
    agent: { id: agentId, model: config.model }, metadata: {}, created_at: 1793404800,
    yuxi: { title: '流式思考展示验证', current_turn: { id: turnId, status: turn().status, current_run_id: runId },
      config, project_id: 'reasoning-project', is_pinned: false } })
  const apiRoute = async route => {
    const raw = route.request().url(), path = raw.replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    let json
    if (path === '/api/auth/me') json = { id: 1, uid: 'reasoning-fixture', username: '展示验证', role: 'user' }
    else if (path === '/api/agent') json = { agents: [agent] }
    else if (path === `/api/agent/${agentId}`) json = agent
    else if (path.endsWith(`/turns/${turnId}`)) json = turn()
    else if (path.endsWith('/items')) json = { data: snapshot, has_more: false, yuxi: { runs: [
      { id: 'history-run', turn_id: 'history-turn', status: 'completed' }, run()] } }
    else if (path === '/api/v1/agents/sessions') json = { data: raw.includes('is_pinned=true') ? [] : [thread()], has_more: false }
    else if (path.endsWith(`/${threadId}`) || path.endsWith('/viewed')) json = thread()
    else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    else if (path.endsWith('/queue')) json = { inputs: [], status: 'ready' }
    else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    else if (path.endsWith('/attachments')) json = { files: [], attachments: [] }
    else if (path === '/api/projects') json = []
    else if (path.includes('/databases')) json = { databases: [] }
    else if (path.includes('/models')) json = { providers: [], models: [], data: [] }
    else if (path.endsWith('/feedback')) json = { items: [] }
    else json = { data: [], files: [], skills: [], projects: [], providers: [], config: {}, first_run: false }
    await route.fulfill({ json })
  }
  await page.route('**/api/**', apiRoute)
  await page.addInitScript(id => {
    if (!['http:', 'https:'].includes(location.protocol)) return
    localStorage.setItem('user_token', 'reasoning-browser-fixture')
    localStorage.setItem('theme', 'light')
    const fetch = window.fetch.bind(window)
    window.fetch = (input, init = {}) => {
      const url = typeof input === 'string' ? input : input.url
      if (url.includes(`/sessions/${id}/events`) && (!init.method || init.method === 'GET')) {
        return Promise.resolve(new Response(new ReadableStream({ start(controller) {
          window.__reasoningWire = controller
          init.signal?.addEventListener('abort', () => { controller.close(); delete window.__reasoningWire }, { once: true })
        } }), { headers: { 'Content-Type': 'text/event-stream' } }))
      }
      return fetch(input, init)
    }
  }, threadId)
  const send = async fields => {
    await page.evaluate(event => window.__reasoningWire.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)), {
      event_id: `reasoning-${++sequence}`, session_id: threadId, turn_id: turnId, yuxi: { run_id: runId }, ...fields
    })
  }
  const hidden = async () => {
    await page.waitForFunction(() => !document.querySelector('.chat-box .reasoning-block'))
    check(!/历史思考原文|检查输入边界|检查工具结果|恢复前思考/.test(await page.locator('.chat-box').innerText()), '旧思考仍在 DOM 中')
  }
  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/agent/${threadId}`)
    await page.getByText('历史回答保留', { exact: true }).waitFor()
    await page.waitForFunction(() => Boolean(window.__reasoningWire))
    await hidden()
    const first = item('first', 3), second = item('second', 4), final = item('final', 6)
    await send({ type: 'agent.session.turn.item.added', item: first })
    await send({ type: 'yuxi.session.turn.reasoning.delta', item_id: first.id, content_index: 0, delta: '检查输入边界' })
    await page.locator('.reasoning-block').getByText('检查输入边界', { exact: true }).waitFor()
    await page.locator('.reasoning-block .tool-header').click()
    await page.locator('.reasoning-content').getByText('检查输入边界', { exact: true }).waitFor()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(async value => { const {useThemeStore}=await import('/src/shared/model/theme.js'); useThemeStore().setTheme(value) }, theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({width, height:900})
        await page.waitForTimeout(350)
        check(await page.locator('.chat-box .reasoning-block').count() === 1, '当前思考应只有一段')
        await page.screenshot({path:`/tmp/yuxi-live-reasoning-${theme}-${width}.png`})
      }
    }
    await send({ type: 'yuxi.session.turn.reasoning.done', item_id: first.id, content_index: 0, text: '检查输入边界' })
    await hidden()
    await send({ type: 'agent.session.turn.output_text.delta', item_id: first.id, content_index: 0, delta: '阶段说明保留' })
    await page.getByText('阶段说明保留', {exact:true}).waitFor()
    await send({ type: 'agent.session.turn.item.added', item: second })
    await send({ type: 'yuxi.session.turn.reasoning.delta', item_id: second.id, content_index: 0, delta: '检查工具结果' })
    await page.locator('.reasoning-block').getByText('检查工具结果', {exact:true}).waitFor()
    const call = { id:'call', type:'function_call', name:'web_search', call_id:'search', arguments:{query:'示例'}, status:'in_progress', turn_id:turnId, yuxi:{run_id:runId,message_id:5} }
    await send({type:'agent.session.turn.item.added',item:call})
    await hidden()
    await send({type:'yuxi.session.turn.reasoning.delta',item_id:second.id,content_index:0,delta:'工具后的迟到增量'})
    await hidden()
    check(await page.locator('.chat-box .tool-calls-container').count() === 1, '隐藏思考同时误删了工具调用')
    await send({type:'agent.session.turn.item.added',item:final})
    await send({type:'yuxi.session.turn.reasoning.delta',item_id:final.id,content_index:0,delta:'恢复前思考'})
    await page.locator('.reasoning-block').getByText('恢复前思考',{exact:true}).waitFor()
    const savedFinal = {...final,yuxi:{...final.yuxi,reasoning:{0:'恢复后完整思考'},completed_reasoning_indices:[0]}}
    snapshot = [history,human,{...first,content:[{type:'output_text',text:'阶段说明保留'}],yuxi:{...first.yuxi,reasoning:{0:'检查输入边界'},completed_reasoning_indices:[0]}},second,call,savedFinal]
    await send({type:'yuxi.session.resync'})
    await hidden()
    await send({type:'yuxi.session.turn.reasoning.delta',item_id:final.id,content_index:0,delta:'不应恢复的迟到增量'})
    await send({type:'yuxi.session.turn.reasoning.delta',item_id:final.id,content_index:2,delta:'最终核验'})
    await page.locator('.reasoning-block').getByText('最终核验',{exact:true}).waitFor()
    check(await page.locator('.reasoning-block').count() === 1, '恢复后拼接了旧思考')
    await send({type:'agent.session.turn.output_text.delta',item_id:final.id,content_index:0,delta:'最终回答保留'})
    await hidden()
    await send({type:'yuxi.session.turn.reasoning.delta',item_id:final.id,content_index:2,delta:'正文后的迟到增量'})
    await hidden()
    const latePreserved = await page.evaluate(async id => {
      const {useSessionRuntimeStore}=await import('/src/modules/session/model/sessionRuntime.js')
      const pinia=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
      const items=useSessionRuntimeStore(pinia).getThreadState(id).itemState.items
      return items.second.yuxi.reasoning[0]==='检查工具结果工具后的迟到增量' &&
        items.final.yuxi.reasoning[2]==='最终核验正文后的迟到增量'
    },threadId)
    check(latePreserved,'迟到的原始思考增量丢失')
    snapshot = snapshot.map(value => ({...value,status:'completed',...(value.id===final.id ? {phase:'final_answer',content:[{type:'output_text',text:'最终回答保留'}]} : {})}))
    completed = true
    await send({type:'agent.session.turn.completed',turn:turn()})
    await page.getByText('最终回答保留',{exact:true}).waitFor()
    await page.getByRole('button',{name:'停止回答',exact:true}).waitFor({state:'hidden'})
    await hidden()
    await page.screenshot({path:'/tmp/yuxi-reasoning-finished.png'})
    await page.reload()
    await page.getByText('最终回答保留',{exact:true}).waitFor()
    await hidden()
    await page.locator('.process-summary').click()
    await page.getByText('阶段说明保留',{exact:true}).waitFor()
    await page.locator('.chat-box .tool-calls-summary').click()
    await hidden()
    const preserved = await page.evaluate(async id => {
      const {useSessionRuntimeStore}=await import('/src/modules/session/model/sessionRuntime.js')
      const pinia=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
      return useSessionRuntimeStore(pinia).getThreadState(id).itemState.items.history.yuxi.reasoning[0]
    },threadId)
    check(preserved==='历史思考原文','隐藏展示删除了原始思考')
    await page.screenshot({path:'/tmp/yuxi-reasoning-history.png'})
    return {currentOnly:true,bodyAndToolsPreserved:true,doneHidden:true,lateHidden:true,resyncHidden:true,historyHidden:true,rawPreserved:true,themes:2,widths:[1440,1024,768]}
  } finally {
    await page.goto('about:blank')
    await page.unroute('**/api/**', apiRoute)
  }
}
