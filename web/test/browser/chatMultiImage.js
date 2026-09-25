// 使用已登录开发环境：playwright-cli -s=<session> run-code --filename=web/test/browser/chatMultiImage.js
// 文件内容由 CLI 作为函数表达式执行，不添加前导分号。
//
// 覆盖聊天框多图（≤10 张）：菜单多选、上限提示不静默丢弃、拖拽分流（图片进
// vision、其它文件进附件）、发送后历史回显。
//
// 前置：`web/test/browser/fixtures/` 下的 imgA.png / imgB.png（图上分别写着 IMG-A、
// IMG-B）与 sample.pdf；需一个支持视觉输入的模型。路径相对仓库根，请在仓库根启动 playwright-cli。
// 脚本只做发送与删除，不修改既有会话数据：发送会新建一个线程，最后清空输入区。
// prettier-ignore
async (page) => {
  const check = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  const web = 'http://localhost:5173'
  const FIXTURES = 'web/test/browser/fixtures'

  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await page.goto(`${web}/agent`)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.waitForTimeout(3000)

  const previews = () => page.locator('.image-preview-list img')
  const attachmentCards = () => page.locator('.attachment-file-card')
  const composer = page.locator('.input-box')

  // toast 生命周期只有 3 秒，而工具往返更久，所以先记账再触发
  await page.evaluate(() => {
    window.__toasts = []
    new MutationObserver((muts) => {
      for (const m of muts) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1 && node.innerText) window.__toasts.push(node.innerText.trim())
        }
      }
    }).observe(document.body, { childList: true, subtree: true })
  })

  // ---- 1. 菜单多选：一次投两张 ----
  await page.getByRole('button', { name: '添加内容' }).click()
  await page.waitForTimeout(600)
  await page.getByText('上传图片', { exact: true }).click()
  await page.waitForTimeout(400)
  const chooser = await page.waitForEvent('filechooser')
  check(chooser.isMultiple(), '菜单里的图片选择器不是多选，一次只能选一张')
  await chooser.setFiles([`${FIXTURES}/imgA.png`, `${FIXTURES}/imgB.png`])
  await page.waitForTimeout(6000)
  check((await previews().count()) === 2, '两张图片没有同时出现在输入区')
  check((await attachmentCards().count()) === 0, '图片被误当成附件')

  // ---- 2. 运行开始后用户消息必须一直带着图片 ----
  // 回归守卫：运行开始时的消息重建只认得服务端请求对象（其中没有图片字段），
  // 一旦重建路径丢图，运行期间用户消息会只剩文字、运行结束后才被历史刷新补回。
  await page.evaluate(() => {
    window.__imgSamples = []
    const timer = setInterval(() => {
      const humans = Array.from(document.querySelectorAll('.message-box.human'))
      window.__imgSamples.push({
        humanBoxes: humans.length,
        msgImgs: document.querySelectorAll('.message-image img').length
      })
    }, 150)
    window.__stopImgSampling = () => clearInterval(timer)
  })
  await page.waitForTimeout(15000)
  await page.evaluate(() => window.__stopImgSampling && window.__stopImgSampling())
  const imgSamples = await page.evaluate(() => window.__imgSamples || [])
  const strandedWithoutImages = imgSamples.filter((s) => s.humanBoxes > 0 && s.msgImgs === 0).length
  check(
    strandedWithoutImages === 0,
    `运行期间有 ${strandedWithoutImages} 次采样显示用户消息没有图片（运行中丢图）`
  )

  // ---- 3. 拖拽分流：图片进 vision，PDF 进附件 ----
  await composer.drop({ files: `${FIXTURES}/imgA.png` })
  await page.waitForTimeout(5000)
  check((await previews().count()) === 3, '拖入图片没有进入图片通道')
  check((await attachmentCards().count()) === 0, '拖入图片被误当成附件')

  await composer.drop({ files: `${FIXTURES}/sample.pdf` })
  await page.waitForTimeout(4000)
  check((await page.locator('.ant-modal-wrap:visible').count()) === 1, '拖入 PDF 没有打开附件弹窗')
  check((await previews().count()) === 3, '拖入 PDF 被误当成图片')
  const cancel = page.locator('.ant-modal-wrap:visible').getByRole('button', { name: '取 消' })
  if (await cancel.count()) {
    await cancel.first().click()
    await page.waitForTimeout(800)
  }

  // ---- 4. 发送：请求体是数组，且模型确实读到了两张 ----
  const posted = []
  page.on('request', (request) => {
    if (request.url().includes('/api/agent/runs') && request.method() === 'POST') {
      try {
        const body = JSON.parse(request.postData() || '{}')
        posted.push(body.image_content)
      } catch {
        posted.push('无法解析请求体')
      }
    }
  })

  await page.locator('.input-box textarea, .user-input.mention-editor').first().click()
  await page.keyboard.type('这两张图分别写了什么字？只回答图片上的文字。')
  await page.waitForTimeout(500)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(45000)

  const sent = posted.at(-1)
  check(Array.isArray(sent) && sent.length === 3, `请求体里的 image_content 不是 3 张的数组：${JSON.stringify(sent)?.slice(0, 60)}`)
  const reply = await page.locator('.message-box, .message-md').allInnerTexts()
  const joinedText = reply.join('\n')
  check(joinedText.includes('IMG-A'), '模型回复里没有第一张图的文字')
  check(joinedText.includes('IMG-B'), '模型回复里没有第二张图的文字')

  // ---- 5. 历史回显：刷新后仍能看到多图，且接口给的是窄投影 ----
  const threadId = page.url().split('/').pop()
  await page.reload()
  await page.waitForTimeout(6000)
  check((await page.locator('.message-image img').count()) === 3, '刷新后历史里没有渲染出三张图片')

  const dto = await page.evaluate(async (id) => {
    const token = localStorage.getItem('user_token')
    const response = await fetch(`/api/chat/thread/${id}/history`, {
      headers: { Authorization: `Bearer ${token}` }
    })
    const data = await response.json()
    // 后端给每条消息都写 image_contents（AI 消息是空数组），只能按类型定位用户消息
    return (data.history || []).find((message) => message.type === 'human')
  }, threadId)
  check(dto, '历史接口没有返回 image_contents 字段')
  check(dto.image_contents.length === 3, '历史里的 image_contents 不是三张')
  check(
    dto.image_contents.every((item) => typeof item === 'string'),
    '历史里的 image_contents 不是字符串数组（不应透传 raw_message 的 part 形状）'
  )

  // ---- 6. 运行中只带图片发送：不得取消运行、不得静默丢图 ----
  // 这一条是回归守卫：发送载荷的键从 image 改为 images 时，若消费端还读旧键，
  // 只带图片发送会被判定成「没有新输入」→ 取消运行并静默丢图。
  const cancels = []
  page.on('request', (request) => {
    if (request.url().includes('/cancel')) cancels.push(request.url())
  })

  // 先起一个会持续一段时间的运行，制造「运行中」的窗口
  await page.locator('.input-box textarea, .user-input.mention-editor').first().click()
  await page.keyboard.type('请写一段 600 字左右的短文，主题自选，不要分点。')
  await page.waitForTimeout(400)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2500)

  const runsBefore = posted.length
  await page.getByRole('button', { name: '添加内容' }).click()
  await page.waitForTimeout(500)
  await page.getByText('上传图片', { exact: true }).click()
  await page.waitForTimeout(300)
  const streamingChooser = await page.waitForEvent('filechooser')
  await streamingChooser.setFiles([`${FIXTURES}/imgA.png`])
  await page.waitForTimeout(5000)
  check((await previews().count()) >= 1, '运行中添加图片没有进入输入区，无法验证该场景')

  await page.keyboard.press('Enter')
  await page.waitForTimeout(6000)

  const sentNewRun = posted.length > runsBefore
  const imagesStillThere = (await previews().count()) >= 1
  check(cancels.length === 0, `运行中只带图片发送触发了取消请求：${cancels.join(', ')}`)
  // 两种结局都合法：发起了新运行，或该状态下发送被挡下。任一情况下图片都不许被静默丢弃。
  check(
    sentNewRun || imagesStillThere,
    '运行中只带图片发送既没有发起新请求，图片也从输入区消失了（静默丢图）'
  )

  // ---- 7. 上限：第 11 张被拒且给出提示 ----
  for (let index = 0; index < 20; index++) {
    const button = page.locator('.image-preview .remove-button')
    if (!(await button.count())) break
    await button.first().click()
    await page.waitForTimeout(250)
  }
  // 上限只看张数，用同一张图重复 11 次即可，不必为测试塞 11 个样本文件
  const eleven = Array.from({ length: 11 }, () => `${FIXTURES}/imgA.png`)
  await page.getByRole('button', { name: '添加内容' }).click()
  await page.waitForTimeout(600)
  await page.getByText('上传图片', { exact: true }).click()
  await page.waitForTimeout(400)
  const secondChooser = await page.waitForEvent('filechooser')
  await secondChooser.setFiles(eleven)
  await page.waitForTimeout(8000)

  const toasts = [...new Set(await page.evaluate(() => window.__toasts || []))]
  check((await previews().count()) === 10, `11 张时不是只收下 10 张，实际 ${await previews().count()}`)
  check(
    toasts.some((text) => text.includes('最多添加 10 张图片')),
    '超出上限时没有给出提示（静默丢弃）'
  )

  // 收尾：清空输入区，不留脏数据给下一次运行
  for (let index = 0; index < 12; index++) {
    const button = page.locator('.image-preview .remove-button')
    if (!(await button.count())) break
    await button.first().click()
    await page.waitForTimeout(250)
  }

  return {
    multiSelect: 2,
    noImageLossDuringRun: true,
    imageOnlySendGuarded: true,
    dragImageToVision: true,
    dragFileToAttachment: true,
    sentImages: sent.length,
    modelReadBoth: true,
    historyRendered: 3,
    limitToast: toasts.find((text) => text.includes('最多添加 10 张图片')) || '',
    threadId
  }
}
