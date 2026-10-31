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
    this.readingPosition = null
    this.resizeScrollTop = null

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
    const container = this.getContainer()
    // 重排产生的 scroll 可能早于 ResizeObserver，不能据此取消原本的贴底意图。
    if (this.hasLayoutChanged(container)) return
    if (container && this.resizeScrollTop === container.scrollTop) {
      this.resizeScrollTop = null
      return
    }
    this.resizeScrollTop = null
    this.rememberReadingPosition()
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

  /** 判断视窗尺寸是否已经变化但尚未补偿。 */
  hasLayoutChanged(container = this.getContainer()) {
    const position = this.readingPosition
    return Boolean(container && position && (
      position.width !== container.clientWidth || position.height !== container.clientHeight
    ))
  }

  /** 保存可见字符的位置，长消息内部也能随自动换行恢复。 */
  rememberReadingPosition() {
    const container = this.getContainer()
    if (!container?.getBoundingClientRect || !container.querySelectorAll) return
    const viewport = container.getBoundingClientRect()
    if (!viewport.width || !viewport.height) return
    const element = [...container.querySelectorAll('.history-display-item')].find(element => {
      const rect = element.getBoundingClientRect()
      return rect.bottom > viewport.top && rect.top < viewport.bottom
    })
    let range = null
    if (element) {
      const walker = container.ownerDocument.createTreeWalker(element, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        if (!node.textContent.trim()) continue
        const candidate = container.ownerDocument.createRange()
        candidate.selectNodeContents(node)
        const rect = candidate.getBoundingClientRect()
        if (!rect.height || rect.bottom <= viewport.top || rect.top >= viewport.bottom) continue
        // 字符的纵坐标随文本顺序递增，二分定位首个可见行，避免逐字符测量。
        let low = 0
        let high = node.length - 1
        while (low < high) {
          const mid = Math.floor((low + high) / 2)
          candidate.setStart(node, mid)
          candidate.setEnd(node, mid + 1)
          if (candidate.getBoundingClientRect().bottom <= viewport.top) low = mid + 1
          else high = mid
        }
        candidate.setStart(node, low)
        candidate.setEnd(node, low + 1)
        range = candidate
        break
      }
    }
    this.readingPosition = {
      width: container.clientWidth,
      height: container.clientHeight,
      element,
      range,
      top: (range || element)?.getBoundingClientRect().top - viewport.top
    }
  }

  /** 重排后保持原字符的视窗位置，原本贴底时继续跟随底部。 */
  handleResize() {
    const container = this.getContainer()
    if (!container?.clientWidth || !container.clientHeight) return
    const position = this.readingPosition
    if (this.shouldAutoScroll) {
      this.scrollContainerToBottom(container)
    } else if (position?.element?.isConnected && container.contains(position.element)) {
      const target = position.range || position.element
      const rect = target.getBoundingClientRect()
      // Markdown 替换文字节点后 Range 可能折叠，不能把零坐标当作新的阅读位置。
      if (rect.height) {
        container.scrollTop += rect.top - container.getBoundingClientRect().top - position.top
      } else {
        this.rememberReadingPosition()
      }
    } else {
      this.rememberReadingPosition()
    }
    this.resizeScrollTop = container.scrollTop
    if (this.readingPosition) {
      this.readingPosition.width = container.clientWidth
      this.readingPosition.height = container.clientHeight
    } else {
      this.rememberReadingPosition()
    }
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
    this.readingPosition = null
    this.resizeScrollTop = null
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
