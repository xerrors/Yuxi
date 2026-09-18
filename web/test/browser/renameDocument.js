// 已登录开发环境：playwright-cli -s=<session> run-code --filename=web/test/browser/renameDocument.js
//
// 覆盖「重命名文件」：行菜单入口、回填、扩展名锁定、列表同步，以及同名文件存在时仍能改回原名。
// 只改展示名，不动内容，因此不触发重新入库。
// 还原放在 finally：脚本中途失败也不会把验证名留在知识库里。
// 前置：知识库中至少有一个非文件夹的文档。
// 文件内容由 CLI 作为函数表达式执行，不添加前导分号。
// prettier-ignore
async (page) => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  // 必须用 SSO 回调所在的来源：OIDC 只会把会话写回 172.25.104.79:5173，
  // localhost:5173 是另一个来源（localStorage 不共享），在那里跑会停在登录页
  const web = 'http://172.25.104.79:5173'
  const rows = () => page.locator('table tbody tr:has(.file-browser-row-actions)')
  const menu = () => page.locator('.file-action-popover')
  const modal = () => page.locator('.ant-modal-wrap:visible')
  const rowsNamed = (name) => page.locator('table tbody tr', { hasText: name })

  // 行菜单是 click 触发的 popover：必须先滚进视口并等它渲染完，
  // 否则点击会落在还没稳定下来的行上（会偶发地点不开或直接点关）。
  const openRowMenu = async (row) => {
    if ((await menu().count()) > 0) {
      await page.keyboard.press('Escape')
      await page.waitForTimeout(300)
    }
    const trigger = row.locator('.file-browser-row-actions button').first()
    await trigger.scrollIntoViewIfNeeded()
    await page.waitForTimeout(200)
    await trigger.click()
    await menu().waitFor({ state: 'visible', timeout: 5000 })
  }

  const openRenameModal = async (row) => {
    await openRowMenu(row)
    await menu().getByRole('button', { name: '重命名', exact: true }).click()
    await page.waitForTimeout(600)
    const input = modal().locator('input')
    check((await input.count()) === 1, '重命名弹窗没有输入框')
    return input
  }

  const submitRename = async (input, name) => {
    await input.fill(name)
    await modal().getByRole('button', { name: /确 定|OK/ }).click()
    await page.waitForTimeout(2500)
  }

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${web}/extensions`)
  await page.locator('.info-card').filter({ hasText: /[1-9]\d* 文件/ }).first().click()
  await page.waitForURL(/\/extensions\/knowledgebase\//)
  await rows().first().waitFor()

  const targetRow = rows().first()
  const originalName = (await targetRow.locator('td').first().innerText()).trim()
  check(Boolean(originalName), '读不到目标文件名')

  const dot = originalName.lastIndexOf('.')
  const stem = dot > 0 ? originalName.slice(0, dot) : originalName
  const ext = dot > 0 ? originalName.slice(dot) : ''
  const renamed = `${stem}-重命名验证${ext}`

  let pendingRestore = false
  let failure = null
  let result = null
  try {
    // 入口：行菜单里必须有「重命名」，且弹窗回填当前名
    const input = await openRenameModal(targetRow)
    check((await input.inputValue()) === originalName, '弹窗未回填当前文件名')

    // 扩展名锁定：改后缀必须被拒，且弹窗不能关闭。
    // 断言具体原因而不是「有失败」——前端对所有 400 都只显示「请求参数错误」，
    // 只有在这里拦下才说明用户真的能看到原因。
    await submitRename(input, `${stem}.zzz`)
    const rejected = await page.locator('.ant-message').innerText().catch(() => '')
    check(rejected.includes('不能修改文件扩展名'), `改后缀没有给出具体原因: ${rejected}`)
    check((await modal().count()) > 0, '扩展名被拒后弹窗被关闭了')

    // 正常改名：只改主干，应当成功并出现在列表里
    pendingRestore = true
    await submitRename(input, renamed)
    check((await modal().count()) === 0, '改名成功后弹窗未关闭')
    check((await rowsNamed(renamed).count()) > 0, `列表里没有出现新文件名 ${renamed}`)

    // 改回原名。库里可能已存在同名文件（上传链路允许同名），
    // 这条断言就是防止重命名再被重名校验挡住而无法还原。
    const restoreInput = await openRenameModal(rowsNamed(renamed).first())
    await submitRename(restoreInput, originalName)
    check((await rowsNamed(renamed).count()) === 0, '改回原名失败，列表里还有验证名')
    pendingRestore = false

    result = { original: originalName, renamed, extensionLocked: true, restored: true }
  } catch (error) {
    failure = error
  } finally {
    if (pendingRestore) {
      try {
        const rescueInput = await openRenameModal(rowsNamed(renamed).first())
        await submitRename(rescueInput, originalName)
        pendingRestore = (await rowsNamed(renamed).count()) > 0
      } catch (cleanupError) {
        // 不能在 finally 里 return/throw：那会盖掉上面真正要暴露的用例失败
        pendingRestore = true
        if (!failure) failure = cleanupError
      }
    }
  }

  if (failure) {
    throw pendingRestore
      ? new Error(`${failure.message}（知识库里仍留有 ${renamed}，需手工改回 ${originalName}）`)
      : failure
  }
  return result
}
