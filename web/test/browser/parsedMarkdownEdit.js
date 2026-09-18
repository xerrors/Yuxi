// 已登录开发环境：playwright-cli -s=<session> run-code --filename=web/test/browser/parsedMarkdownEdit.js
//
// 覆盖「编辑解析产物」的交互：入口在筛选视图下必须可用、编辑态可进入、分栏同步滚动、
// 未保存草稿不被 ESC 静默丢弃。
//
// 前置：知识库中至少有一个「待入库」(parsed) 状态的文档。脚本全程只读 + 取消，
// 不会保存任何修改，也不改动词料状态。
// 文件内容由 CLI 作为函数表达式执行，不添加前导分号。
// prettier-ignore
async (page) => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const web = 'http://localhost:5173'

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

  const editor = page.locator('textarea.markdown-editor')
  const preview = page.locator('.markdown-editor-preview')
  const toolbar = page.locator('.markdown-edit-toolbar')
  check((await editor.count()) === 1, '筛选视图下点「编辑文件」没有进入编辑态')
  check((await preview.count()) === 1, '编辑态缺少右侧预览')
  check((await toolbar.innerText()).includes('编辑中'), '编辑态缺少「编辑中」标识')

  // 分栏同步滚动：两侧内容高度不同，按滚动比例映射；程序化写入会触发回声事件，
  // 实现需忽略它，否则左右会互相驱动来回抖动。
  await editor.evaluate((el) => {
    const parts = []
    for (let i = 1; i <= 60; i += 1) parts.push(`## 第 ${i} 节\n\n用于把内容撑长以便验证同步滚动的段落。\n`)
    el.value = parts.join('\n')
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await page.waitForTimeout(800)
  check((await toolbar.innerText()).includes('已修改'), '修改后未标记「已修改」')

  const sync = await page.evaluate(() => {
    const ta = document.querySelector('textarea.markdown-editor')
    const pv = document.querySelector('.markdown-editor-preview')
    if (!ta || !pv) return null
    const range = (el) => el.scrollHeight - el.clientHeight
    const ratio = (el) => (range(el) > 0 ? el.scrollTop / range(el) : 0)
    if (range(ta) <= 0) return { scrollable: false }
    ta.scrollTop = Math.round(range(ta) * 0.5)
    ta.dispatchEvent(new Event('scroll'))
    return { scrollable: true, editorRatio: ratio(ta), previewRatio: ratio(pv) }
  })
  if (sync && sync.scrollable) {
    check(Math.abs(sync.previewRatio - sync.editorRatio) < 0.05, `分栏未同步滚动：编辑器 ${sync.editorRatio.toFixed(3)} vs 预览 ${sync.previewRatio.toFixed(3)}`)
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

  return { target: targetName, filteredRows: rowCount, syncVerified: Boolean(sync && sync.scrollable), escGuard: true, savedNothing: true }
}
