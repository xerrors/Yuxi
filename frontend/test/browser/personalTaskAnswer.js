// 先登录独立验收环境，准备 /tmp/yuxi-personal-browser-upload.txt；真实问询、图片和附件链路。
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const headers = { Authorization: 'Bearer ' + await page.evaluate(() => localStorage.getItem('user_token')) }
  const api = async path => {
    const response = await page.request.get(origin + '/api/' + path, { headers })
    check(response.ok(), '读取失败：' + path)
    return response.json()
  }
  const poll = async (read, matches) => {
    const deadline = Date.now() + 40000
    while (Date.now() < deadline) {
      const value = await read()
      if (matches(value)) return value
      await page.waitForTimeout(200)
    }
    throw new Error('持久状态未在期限内满足断言')
  }
  await page.goto(origin + '/assistant')
  const id = (await api('agents/personal')).assistant.thread_id
  const input = page.getByRole('textbox', { name: '向你的助理发送消息', exact: true })
  await input.waitFor()
  await poll(() => input.getAttribute('contenteditable'), value => value === 'true')
  const previous = new Set((await api('agents/personal/sessions')).sessions.map(task => task.session_id))
  await input.fill('PERSONAL_DELEGATE ASK_TASK 需要确认格式')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const task = await poll(async () => (await api('agents/personal/sessions')).sessions.find(row => !previous.has(row.session_id)), row => row?.turn_status === 'waiting')
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  await page.getByRole('button', { name: '刷新委派任务', exact: true }).click()
  await page.locator(`.personal-profile .task-row[data-session-id="${task.session_id}"]`).click()
  const trace = page.locator('.panel-session-chat:visible')
  await trace.getByRole('dialog', { name: /请确认任务的输出格式/ }).waitFor()
  check(await trace.locator('[contenteditable="true"]').count() === 0, '只读轨迹出现普通编辑器')
  await trace.getByText('简短记录', { exact: true }).click()
  await page.screenshot({ path: '/tmp/yuxi-personal-task-question.png' })
  await trace.getByRole('button', { name: '提交', exact: true }).click()
  await poll(async () => (await api('agents/personal/sessions')).sessions.find(row => row.session_id === task.session_id), row => row?.turn_status === 'completed')
  await trace.getByText('PERSONAL_ANSWERED_TASK_OK', { exact: true }).waitFor()
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  await page.getByRole('button', { name: '添加内容', exact: true }).click()
  await page.getByText('上传附件', { exact: true }).click()
  const upload = page.getByRole('dialog', { name: '添加附件', exact: true })
  await upload.locator('input[type="file"]').setInputFiles('/tmp/yuxi-personal-browser-upload.txt')
  await upload.getByRole('button', { name: '添加附件', exact: true }).click()
  await page.getByText('yuxi-personal-browser-upload.txt', { exact: true }).first().waitFor()
  await input.evaluate(element => {
    const base64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE0sAAAAASUVORK5CYII='
    const transfer = new DataTransfer()
    transfer.items.add(new File([Uint8Array.from(atob(base64), c => c.charCodeAt(0))], 'personal-proof.png', { type: 'image/png' }))
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }))
  })
  await page.locator('.image-preview-list .image-preview-wrapper:not(.image-uploading-tile)').waitFor()
  await input.fill('PERSONAL_SEND 核对图片与附件')
  const response = page.waitForResponse(res => res.request().method() === 'POST' && res.url().endsWith('/events'))
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const sent = await response
  check(sent.status() === 202, '带图片附件的消息未被接收')
  const body = sent.request().postDataJSON().events[0]
  check(body.input[0].content.some(part => part.type === 'input_image'), '图片未随真实 Input 发送')
  check(body.yuxi.attachment_file_ids.length === 1, '附件未绑定同一 Input')
  const receipt = await sent.json()
  const consumed = await poll(() => api(`v1/agents/sessions/${id}/inputs/${receipt.input_id}`), value => value.status === 'consumed')
  const result = await poll(() => api(`v1/agents/sessions/${id}/turns/${consumed.turn_id}`), value => ['completed', 'failed'].includes(value.status))
  check(result.status === 'completed', '带附件运行失败')
  const items = await api(`v1/agents/sessions/${id}/items?limit=100&order=desc`)
  check(items.data.some(item => item.role === 'user' && item.yuxi?.input_id === receipt.input_id && item.content.some(part => part.type === 'input_image')), '回读图片不属于该 Input')
  const stored = items.data.find(item => item.role === 'user' && item.yuxi?.input_id === receipt.input_id)
  check(stored.yuxi.attachments.length === 1, '附件未从持久历史回读')
  console.log('真实浏览器通过：只读任务问询提交恢复、附件上传、图片粘贴、同 Input 持久回读。')
}
