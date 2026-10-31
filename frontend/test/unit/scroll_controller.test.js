import assert from 'node:assert/strict'
import test from 'node:test'

import ScrollController from '../../src/modules/session/model/scrollController.js'

test('ScrollController 在用户向上滚动离开底部时停用自动滚动', () => {
  let scrollTop = 500
  const mockContainer = {
    scrollHeight: 1000,
    clientHeight: 400,
    get scrollTop() {
      return scrollTop
    },
    set scrollTop(val) {
      scrollTop = val
    },
    scrollTo({ top }) {
      scrollTop = top
    }
  }

  const controller = new ScrollController(() => mockContainer, {
    threshold: 80,
    scrollDelay: 20
  })

  // 初始在底部 (1000 - 520 - 400 = 80 <= threshold)
  scrollTop = 520
  assert.equal(controller.isAtBottom(), true)
  assert.equal(controller.shouldAutoScroll, true)

  // 用户向上滚动离开底部 (1000 - 200 - 400 = 400 > 80)
  scrollTop = 200
  controller.handleScroll()
  assert.equal(controller.isAtBottom(), false)
  assert.equal(controller.shouldAutoScroll, false)

  // 用户滚动回到底部附近 (1000 - 550 - 400 = 50 <= 80)
  scrollTop = 550
  controller.handleScroll()
  assert.equal(controller.isAtBottom(), true)
  assert.equal(controller.shouldAutoScroll, true)
})

test('ScrollController 在 shouldAutoScroll 为 false 时 scrollToBottom 不执行滚动', async () => {
  let scrollCalled = false
  let scrollTop = 100
  const mockContainer = {
    scrollHeight: 1000,
    clientHeight: 400,
    get scrollTop() {
      return scrollTop
    },
    set scrollTop(val) {
      scrollTop = val
    },
    scrollTo({ top }) {
      scrollCalled = true
      scrollTop = top
    }
  }

  const controller = new ScrollController(() => mockContainer, {
    threshold: 80,
    scrollDelay: 20,
    retryDelays: []
  })

  // 用户向上滚动，离开底部
  controller.handleScroll()
  assert.equal(controller.shouldAutoScroll, false)

  await controller.scrollToBottom(false)
  assert.equal(scrollCalled, false)
  assert.equal(scrollTop, 100)

  // 强制滚动仍然执行
  await controller.scrollToBottom(true)
  assert.equal(scrollCalled, true)
  assert.equal(scrollTop, 600)
})

test('重排先触发 scroll 时仍保留贴底意图，恢复后贴到新的底部', () => {
  const container = {
    clientWidth: 600, clientHeight: 400, scrollHeight: 3000, scrollTop: 600,
    scrollTo({ top }) { this.scrollTop = top }
  }
  const controller = new ScrollController(container, { scrollDelay: 10 })
  controller.readingPosition = { width: 1000, height: 400 }
  controller.handleScroll()
  assert.equal(controller.shouldAutoScroll, true)
  controller.handleResize()
  assert.equal(container.scrollTop, 2600)
  assert.equal(controller.shouldAutoScroll, true)
  controller.cleanup()
})

test('重排按字符坐标补偿长消息内部位置，手动上滚后允许更新阅读意图', () => {
  const element = { isConnected: true }
  const container = {
    clientWidth: 600, clientHeight: 400, scrollHeight: 5000, scrollTop: 1000,
    contains: value => value === element,
    getBoundingClientRect: () => ({ top: 50 })
  }
  const controller = new ScrollController(container, { scrollDelay: 10 })
  controller.disableAutoScroll()
  // 独立坐标：重排后同一字符落在内容高度 1800px，原来距视窗顶部 8px。
  controller.readingPosition = {
    width: 1000, height: 400, element, top: 8,
    range: { getBoundingClientRect: () => ({ top: 50 + 1800 - container.scrollTop, height: 20 }) }
  }
  controller.handleResize()
  assert.equal(container.scrollTop, 1792)
  controller.handleScroll()
  assert.equal(controller.shouldAutoScroll, false, '布局补偿不改变上滚阅读意图')
  container.scrollTop = 1700
  controller.handleScroll()
  assert.equal(controller.shouldAutoScroll, false)
  assert.equal(controller.isUserScrolling, true, '补偿之后的主动上滚不能被吞掉')
  controller.cleanup()
  assert.equal(controller.readingPosition, null)
})

test('隐藏容器不会被尺寸观察拉到底部或覆盖阅读位置', () => {
  const container = { clientWidth: 0, clientHeight: 0, scrollTop: 123 }
  const controller = new ScrollController(container)
  const position = { width: 900, height: 700 }
  controller.readingPosition = position
  controller.handleResize()
  assert.equal(container.scrollTop, 123)
  assert.equal(controller.readingPosition, position)
  controller.cleanup()
})

test('文字节点被替换后的折叠 Range 不会把视窗拉向零坐标', () => {
  const element = { isConnected: true }
  const container = {
    clientWidth: 600, clientHeight: 400, scrollTop: 1234,
    contains: () => true, getBoundingClientRect: () => ({ top: 50 })
  }
  const controller = new ScrollController(container)
  controller.disableAutoScroll()
  controller.readingPosition = {
    element, top: 8, width: 900, height: 400,
    range: { getBoundingClientRect: () => ({ top: 0, height: 0 }) }
  }
  controller.handleResize()
  assert.equal(container.scrollTop, 1234)
  controller.cleanup()
})
