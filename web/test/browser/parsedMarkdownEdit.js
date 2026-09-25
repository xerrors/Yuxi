// 已登录开发环境：playwright-cli -s=<session> run-code --filename=web/test/browser/parsedMarkdownEdit.js
//
// 覆盖「编辑解析产物」的交互：入口在筛选视图下必须可用、编辑态可进入、未保存草稿不被 ESC
// 静默丢弃、保存请求挂起期间禁止继续编辑且草稿不被误标为已保存。若该文档有可预览原件
// （存在「源文件 / Markdown」两个视图），还验证编辑期间视图切换被禁用。
//
// 前置：知识库中至少有一个「待入库」(parsed) 状态的文档。脚本不改动知识库数据：
// 编辑一律取消，唯一一次「保存」被路由拦截扣住并 abort，不会到达服务端。
// 文件内容由 CLI 作为函数表达式执行，不添加前导分号。
// prettier-ignore
async (page) => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  // 必须用 SSO 回调所在的来源：OIDC 只会把会话写回 172.25.104.79:5173，
  // localhost:5173 是另一个来源（localStorage 不共享），在那里跑会停在登录页
  const web = 'http://172.25.104.79:5173'

  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await page.setViewportSize({ width: 1440, height: 900 })

  // 进入一个**有文件**的知识库：列表里可能同时存在空库（如刚建的测试库），
  // 空库没有文件行，无法验证文件行菜单。
  await page.goto(`${web}/extensions`)
  await page.locator('.info-card').filter({ hasText: /[1-9]\d* 文件/ }).first().click()
  await page.waitForURL(/\/extensions\/knowledgebase\//)
  await page.locator('.file-browser-row-actions button').first().waitFor()

  // 关键场景：按状态筛选后，行菜单里的「编辑文件」必须仍然可用。
  // 筛选会让 fileBrowser.recursive 为真，若处理器上挂了目录树相关的守卫，
  // 这里会变成「按钮可见但点了没反应」。
  await page.getByTitle('筛选状态').click()
  await page.getByRole('menuitem', { name: '待入库', exact: true }).click()
  await page.waitForTimeout(1200)
  const rows = page.locator('table tbody tr:has(.file-browser-row-actions)')
  const rowCount = await rows.count()
  check(rowCount > 0, '筛选「待入库」后没有文档，无法验证编辑入口')

  const targetName = (await rows.first().locator('td').first().innerText()).trim()
  check(Boolean(targetName), '读不到目标文档名')

  await rows.first().locator('.file-browser-row-actions button').first().click()
  const editEntry = page.getByRole('button', { name: '编辑文件', exact: true })
  check((await editEntry.count()) > 0, '行菜单里没有「编辑文件」')
  await editEntry.click()
  await page.waitForTimeout(1500)

  // 编辑态复用 AgentFilePreview：它的 textarea 与浮动操作条就是编辑态的判据
  const editor = page.locator('.ant-modal-content textarea.file-edit-textarea')
  const floating = page.locator('.edit-floating-actions')
  check((await editor.count()) === 1, '筛选视图下点「编辑文件」没有进入编辑态')

  await editor.evaluate((el) => {
    el.value = '# 修订后的标题\n\n这是人工修正后的内容。\n'
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await page.waitForTimeout(800)
  check((await floating.count()) === 1, '修改后未出现浮动操作条')
  check((await floating.innerText()).includes('未保存'), '修改后浮动操作条未标记「未保存」')

  // 编辑期间必须挡住视图切换：草稿存在 AgentFilePreview 的局部状态里，而 markdown 分支是
  // v-if 渲染的，切到「源文件」会让它连同草稿一起被卸载，且切换本身没有任何确认步骤。
  // 只有存在可预览原件（源文件 + Markdown 两种视图）的文档才有切换器，故条件执行。
  const viewItems = page.locator('.view-controls .ant-segmented-item')
  if ((await viewItems.count()) > 1) {
    check(
      (await page.locator('.view-controls .ant-segmented-disabled').count()) === 1,
      '编辑期间视图切换未被禁用，草稿会被静默丢弃'
    )
    await viewItems.nth(0).click({ force: true })
    await page.waitForTimeout(1200)
    check((await editor.count()) === 1, '编辑期间切视图把编辑态（连同草稿）丢掉了')
    check((await editor.inputValue()).includes('这是人工修正后的内容'), '切视图后草稿丢失')
  }

  // ESC 不得静默丢弃草稿
  await page.keyboard.press('Escape')
  await page.waitForTimeout(1200)
  const confirm = page.locator('.ant-modal-confirm:visible')
  check((await confirm.count()) > 0, '有未保存草稿时按 ESC 被静默关闭')
  check((await confirm.innerText()).includes('放弃未保存的修改'), 'ESC 弹出的不是未保存确认')
  check((await editor.count()) === 1, 'ESC 后编辑态已被关闭')

  await confirm.getByRole('button', { name: '继续编辑' }).click()
  await page.waitForTimeout(900)
  check((await editor.count()) === 1, '选择「继续编辑」后编辑态丢失')

  // 保存挂起窗口：扣住 PUT 不放行，核对「保存期间禁止编辑」这条收敛方式真的生效，
  // 且草稿不会被错误标记为已保存。abort 收尾，请求不会到达服务端。
  let held = null
  await page.route('**/api/knowledge/databases/*/documents/*/content', async (route) => {
    if (route.request().method() === 'PUT') {
      held = route
      return
    }
    await route.continue()
  })

  await floating.getByRole('button', { name: '保存', exact: true }).click()
  await page.waitForTimeout(1200)
  check(Boolean(held), '没有截到保存请求，无法验证挂起窗口')
  check(await editor.isDisabled(), '保存请求挂起期间 textarea 仍可编辑，草稿可能被覆盖')
  check(
    (await page.locator('.edit-floating-actions button[aria-label="保存中"]').count()) === 1,
    '保存期间保存按钮未被禁用'
  )
  check((await floating.innerText()).includes('未保存'), '保存期间草稿被错误标记为已保存')

  await held.abort()
  await page.waitForTimeout(1500)
  check(!(await editor.isDisabled()), '保存失败后 textarea 未恢复可编辑')
  check(
    (await editor.inputValue()).includes('这是人工修正后的内容'),
    '保存失败后草稿丢失'
  )
  check((await floating.innerText()).includes('未保存'), '保存失败后草稿被标记为已保存')
  await page.unrouteAll({ behavior: 'ignoreErrors' })

  // 关闭并放弃：不得落库
  await page.locator('.custom-close-btn').click()
  await page.waitForTimeout(900)
  const discard = page.locator('.ant-modal-confirm:visible')
  if ((await discard.count()) > 0) {
    await discard.getByRole('button', { name: /放弃并关闭/ }).click()
    await page.waitForTimeout(1200)
  }
  check((await page.locator('.ant-modal-wrap:visible').count()) === 0, '弹层未关闭')

  // 重新筛回「待入库」并核对**被编辑的那个文件**仍在其中：放弃编辑不应改动它的状态。
  await page.getByTitle('筛选状态').click()
  await page.getByRole('menuitem', { name: '待入库', exact: true }).click()
  await page.waitForTimeout(1500)
  const stillParsed = await page.locator('table tbody tr', { hasText: targetName }).count()
  check(stillParsed > 0, `放弃编辑后 ${targetName} 已不在「待入库」中，状态被改动`)

  await page.getByTitle('筛选状态').click()
  await page.getByRole('menuitem', { name: '全部状态', exact: true }).click()
  await page.waitForTimeout(800)

  return {
    target: targetName,
    filteredRows: rowCount,
    reusedEditor: true,
    escGuard: true,
    viewSwitchGuard: true,
    saveWindowGuard: true,
    savedNothing: true
  }
}
