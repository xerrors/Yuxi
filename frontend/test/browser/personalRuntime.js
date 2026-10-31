// 独立环境、真实 API/worker/数据库，不拦截请求；使用 personal_assistant_replay_server。
// 登录测试账号后运行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/personalRuntime.js
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const poll = async (read, done, label) => {
    const deadline = Date.now() + 40000
    while (Date.now() < deadline) {
      const value = await read()
      if (done(value)) return value
      await page.waitForTimeout(200)
    }
    throw new Error('超时：' + label)
  }
  const token = await page.evaluate(() => localStorage.getItem('user_token'))
  check(token, '请先用真实测试账号登录')
  const headers = { Authorization: 'Bearer ' + token }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const api = async path => {
    const response = await page.request.get(origin + '/api/' + path, { headers })
    check(response.ok(), path + ' 返回 ' + response.status())
    return response.json()
  }
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.goto(origin + '/assistant')
  let current = await api('agents/personal')
  if (!current.assistant) {
    await page.getByRole('textbox', { name: '助理名称', exact: true }).fill('小析')
    await page.getByRole('button', { name: '选择头像 2', exact: true }).click()
    await page.screenshot({ path: '/tmp/yuxi-personal-setup.png' })
    await page.getByRole('button', { name: '创建助理', exact: true }).click()
    current = await poll(() => api('agents/personal'), value => value.assistant, '创建唯一助理')
  }
  const id = current.assistant.thread_id
  const main = page.locator('.personal-workspace > .chat > .chat-content-container > .chat-main')
  const input = page.getByRole('textbox', { name: '向你的助理发送消息', exact: true })
  await input.waitFor()
  check(await page.getByRole('link', { name: '个人助理', exact: true }).count() === 1, '个人入口应为唯一导航')
  check(await page.locator('.nav').getByRole('link', { name: '个人助理', exact: true }).count() === 1, '入口应在主导航中')
  const send = async text => {
    await poll(() => input.getAttribute('contenteditable'), value => value === 'true', '输入恢复可编辑')
    await input.fill(text)
    const accepted = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/events'))
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    const response = await accepted
    check(response.status() === 202, '消息提交未接受')
    const receipt = await response.json()
    check(response.request().postDataJSON().events[0].yuxi.mode === 'steer', '发送没有使用 steer')
    return receipt
  }
  const terminal = async receipt => {
    const consumed = await poll(() => api(`v1/agents/sessions/${id}/inputs/${receipt.input_id}`), value => value.status === 'consumed', 'Input 消费')
    return poll(() => api(`v1/agents/sessions/${id}/turns/${consumed.turn_id}`), value => ['completed', 'failed', 'cancelled'].includes(value.status), '真实 Turn 终态')
  }
  await terminal(await send('PERSONAL_SEND 浏览器显式沟通验收'))
  await poll(() => main.innerText(), text => text.includes('这是通过 send_to_user 发出的消息。'), '成功发送实时显示')
  check(!(await main.innerText()).includes('PERSONAL_INTERNAL_'), '内部正文泄漏到主聊天')
  await terminal(await send('PERSONAL_SILENT 只检查，不需要回复'))
  const raw = await api(`v1/agents/sessions/${id}/items?limit=100&order=asc`)
  check(JSON.stringify(raw.data).includes('PERSONAL_INTERNAL_SILENT_CONTENT'), '静默运行没有持久正文')
  check(!(await main.innerText()).includes('PERSONAL_INTERNAL_'), '静默正文泄漏')
  await page.reload()
  await input.waitFor()
  await main.getByText('这是通过 send_to_user 发出的消息。', { exact: true }).first().waitFor({timeout:10000})
  check(!(await main.innerText()).includes('PERSONAL_INTERNAL_'), '刷新后内部正文泄漏')
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  await page.getByRole('button', { name: '编辑资料', exact: true }).click()
  await page.getByRole('textbox', { name: '交流偏好', exact: true }).fill('先说结论，区分事实与推断。')
  await page.getByRole('button', { name: /保\s*存/ }).click()
  await poll(() => api('agents/personal'), value => value.assistant.config_json.context.system_prompt === '先说结论，区分事实与推断。', '偏好回读')
  const priorTasks = new Set((await api('agents/personal/sessions')).sessions.map(task => task.session_id))
  await terminal(await send('PERSONAL_DELEGATE 请整理任务记录'))
  const task = await poll(async () => (await api('agents/personal/sessions')).sessions.find(row => !priorTasks.has(row.session_id)), row => row?.turn_status === 'completed', '普通执行 Agent 完成任务')
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  check(await page.locator(`.personal-profile .task-row[data-session-id="${task.session_id}"]`).count() === 0, '已完成任务仍显示在助理卡片')
  await page.keyboard.press('Escape')
  await page.goto(origin + '/assistant?session_id=' + task.session_id)
  const trace = page.locator('.panel-session-chat:visible')
  await trace.getByText('PERSONAL_EXECUTOR_RESULT_OK', { exact: true }).waitFor({ timeout: 15000 })
  await trace.locator('.process-summary').first().click()
  await trace.getByRole('button', { name: '调用: 写入文件', exact: true }).click()
  await trace.getByText('写入文件', { exact: true }).waitFor()
  check(await trace.locator('input,[contenteditable="true"]').count() === 0, '任务轨迹出现普通输入')
  check(page.url().split('?')[0] === origin + '/assistant', '查看 root 任务离开了助理页面')
  check(page.url().includes('session_id=' + task.session_id), '轨迹未保存稳定 session 身份')
  await page.waitForTimeout(350) // 等待侧栏过渡完成后截图。
  await page.screenshot({ path: '/tmp/yuxi-personal-task-trace.png' })
  await page.reload()
  await trace.getByText('PERSONAL_EXECUTOR_RESULT_OK', { exact: true }).waitFor({ timeout: 15000 })
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  await poll(async () => page.url().split('?')[1] || '', search => !search.includes('session_id'), '关闭面板清理路由')
  await page.getByRole('button', { name: '查看助理完整轨迹', exact: true }).click()
  await trace.getByText('PERSONAL_INTERNAL_SILENT_CONTENT', { exact: true }).first().waitFor({ timeout: 15000 })
  check(!(await main.innerText()).includes('PERSONAL_INTERNAL_'), '打开轨迹后主聊天发生泄漏')
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  const busy = await send('PERSONAL_BLOCK 处理中的任务')
  await poll(() => api(`v1/agents/sessions/${id}/inputs/${busy.input_id}`), value => value.status === 'consumed', '运行中接收 steer')
  check(await page.getByRole('button', { name: '停止回答', exact: true }).count() === 0, '助理不应提供停止')
  const follow = await send('PERSONAL_FOLLOW 换个方向')
  await main.getByText('PERSONAL_FOLLOW 换个方向', { exact: true }).last().waitFor()
  await terminal(follow)
  await main.getByText('已收到新的引导。', { exact: true }).last().waitFor({ timeout: 10000 })
  await page.getByRole('button', { name: '添加内容', exact: true }).click()
  await page.getByText('上传附件', { exact: true }).waitFor()
  await page.getByText('添加图片', { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await input.fill('第一行\n第二行\n第三行')
  const multi = (await input.boundingBox()).height
  await input.fill('单行')
  check((await input.boundingBox()).height < multi, '输入框未随内容恢复单行')
  await input.fill('')
  for (const width of [1440, 1024, 768, 375]) {
    await page.setViewportSize({ width, height: 960 })
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), width + ' 出现横向溢出')
  }
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.waitForTimeout(350) // 响应式侧栏宽度过渡不属于最终界面。
  await page.screenshot({ path: '/tmp/yuxi-personal-runtime-light.png' })
  await page.evaluate(() => localStorage.setItem('theme', 'dark'))
  await page.reload()
  await input.waitFor()
  await main.getByText('已收到新的引导。', { exact: true }).last().waitFor({timeout:15000})
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  await page.getByRole('button', { name: '编辑资料', exact: true }).waitFor()
  await page.waitForTimeout(350)
  await page.screenshot({ path: '/tmp/yuxi-personal-runtime-dark.png' })
  await page.keyboard.press('Escape')
  if (await page.getByRole('button', { name: '展开侧边栏', exact: true }).isVisible()) await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  check(await page.locator('.nav').getByRole('link', { name: '个人助理', exact: true }).isVisible(), '收起后缺少助理入口')
  await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  await page.evaluate(() => localStorage.setItem('theme', 'light'))
  await page.reload()
  await input.waitFor()
  console.log('真实浏览器验收通过：显式沟通、静默、独立任务、右侧完整轨迹、恢复、steer、资料、附件入口、输入自适应、主题及宽度。')
}
