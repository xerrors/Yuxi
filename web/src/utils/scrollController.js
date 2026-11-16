import { nextTick } from 'vue'

/**
 * 滚动控制工具类
 */
export class ScrollController {
  constructor(containerSelector = '.chat', options = {}) {
    this.containerSelector = containerSelector
    this.options = {
      threshold: 40,
      scrollDelay: 100,
      retryDelays: [50, 150],
      ...options
    }

    this.scrollTimer = null
    this.scrollRetryTimers = []
    this.programmaticScrollTimer = null
    this.isUserScrolling = false
    this.shouldAutoScroll = true
    this.isProgrammaticScroll = false

    // Bind the context of 'this' for the event handler
    this.handleScroll = this.handleScroll.bind(this)
  }

  /**
   * 获取滚动容器
   * @returns {Element|null}
   */
  getContainer() {
    if (typeof this.containerSelector === 'function') {
      return this.containerSelector()
    }
    if (typeof this.containerSelector === 'string') {
      return document.querySelector(this.containerSelector)
    }
    return this.containerSelector || null
  }

  /**
   * 检查是否在底部
   * @returns {boolean}
   */
  isAtBottom() {
    const container = this.getContainer()
    if (!container) return false

    const { threshold } = this.options
    return this.getBottomOffset(container) <= threshold
  }

  getBottomOffset(container = this.getContainer()) {
    if (!container) return Number.POSITIVE_INFINITY
    return Math.max(0, container.scrollHeight - container.scrollTop - container.clientHeight)
  }

  cancelPendingScrolls() {
    this.scrollRetryTimers.forEach((timer) => clearTimeout(timer))
    this.scrollRetryTimers = []
  }

  markProgrammaticScroll() {
    if (this.programmaticScrollTimer) {
      clearTimeout(this.programmaticScrollTimer)
    }
    this.isProgrammaticScroll = true
    this.programmaticScrollTimer = setTimeout(() => {
      this.isProgrammaticScroll = false
      this.programmaticScrollTimer = null
    }, this.options.scrollDelay)
  }

  /**
   * 处理滚动事件
   */
  handleScroll() {
    if (this.scrollTimer) {
      clearTimeout(this.scrollTimer)
    }

    // 如果是程序性滚动，仍根据当前位置同步状态，避免标记滞留后误吞用户上滚。
    if (this.isProgrammaticScroll) {
      const atBottom = this.isAtBottom()
      this.shouldAutoScroll = atBottom
      this.isProgrammaticScroll = false
      if (this.programmaticScrollTimer) {
        clearTimeout(this.programmaticScrollTimer)
        this.programmaticScrollTimer = null
      }
      if (!atBottom) {
        this.cancelPendingScrolls()
        this.isUserScrolling = true
        this.scrollTimer = setTimeout(() => {
          this.isUserScrolling = false
        }, this.options.scrollDelay)
      }
      return
    }

    this.cancelPendingScrolls()

    // 标记用户正在滚动
    this.isUserScrolling = true

    // 检查是否在底部
    this.shouldAutoScroll = this.isAtBottom()

    // 滚动结束后一段时间重置用户滚动状态
    this.scrollTimer = setTimeout(() => {
      this.isUserScrolling = false
    }, this.options.scrollDelay)
  }

  /**
   * 等待 DOM 布局稳定
   * @returns {Promise<void>}
   */
  async waitForLayoutStable() {
    if (typeof requestAnimationFrame !== 'function') return
    // 使用 requestAnimationFrame 确保 DOM 渲染完成
    await new Promise((resolve) => requestAnimationFrame(resolve))
    await new Promise((resolve) => requestAnimationFrame(resolve))
  }

  scrollContainerToBottom(container, behavior = 'auto') {
    if (!container) return
    this.markProgrammaticScroll()
    container.scrollTo({
      top: Math.max(0, container.scrollHeight - container.clientHeight),
      behavior
    })
  }

  /**
   * 智能滚动到底部
   * @param {boolean} force - 是否强制滚动
   */
  async scrollToBottom(force = false) {
    await nextTick()
    // 等待 DOM 布局稳定
    await this.waitForLayoutStable()

    // 只有在应该自动滚动时才执行（除非强制）
    if (!force && !this.shouldAutoScroll) return

    const container = this.getContainer()
    if (!container) return

    this.cancelPendingScrolls()

    this.scrollContainerToBottom(container, 'auto')

    // 动态内容仍可能在下一帧补齐高度，保留少量重试，但用户手动滚动会立即取消。
    this.options.retryDelays.forEach((delay) => {
      const timer = setTimeout(() => {
        if (force || this.shouldAutoScroll) {
          this.scrollContainerToBottom(container, 'auto')
        }
      }, delay)
      this.scrollRetryTimers.push(timer)
    })
  }

  async scrollToBottomStaticForce() {
    const container = this.getContainer()
    if (!container) return

    this.cancelPendingScrolls()
    await nextTick()
    await this.waitForLayoutStable()
    this.scrollContainerToBottom(container, 'auto')
    this.shouldAutoScroll = true
  }

  /**
   * 启用自动滚动
   */
  enableAutoScroll() {
    this.shouldAutoScroll = true
  }

  /**
   * 禁用自动滚动
   */
  disableAutoScroll() {
    this.shouldAutoScroll = false
  }

  /**
   * 获取滚动状态
   */
  getScrollState() {
    return {
      isUserScrolling: this.isUserScrolling,
      shouldAutoScroll: this.shouldAutoScroll,
      isAtBottom: this.isAtBottom()
    }
  }

  /**
   * 清理定时器
   */
  cleanup() {
    if (this.scrollTimer) {
      clearTimeout(this.scrollTimer)
      this.scrollTimer = null
    }
    if (this.programmaticScrollTimer) {
      clearTimeout(this.programmaticScrollTimer)
      this.programmaticScrollTimer = null
    }
    this.cancelPendingScrolls()
  }

  /**
   * 重置滚动状态
   */
  reset() {
    this.cleanup()
    this.isUserScrolling = false
    this.shouldAutoScroll = true
    this.isProgrammaticScroll = false
  }
}

export default ScrollController
