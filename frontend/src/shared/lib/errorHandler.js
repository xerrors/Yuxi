import { message } from 'ant-design-vue'

/**
 * 统一错误处理工具类
 */
export class ErrorHandler {
  /**
   * 处理通用错误
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static handleError(error, context = '操作', options = {}) {
    const {
      showMessage = true,
      logToConsole = true,
      customMessage = null,
      severity = 'error'
    } = options

    // 控制台日志
    if (logToConsole) {
      console.error(`${context}失败:`, error)
    }

    // 用户提示
    if (showMessage) {
      const displayMessage = customMessage || this.getErrorMessage(error, context)

      switch (severity) {
        case 'warning':
          message.warning(displayMessage)
          break
        case 'info':
          message.info(displayMessage)
          break
        case 'error':
        default:
          message.error(displayMessage)
          break
      }
    }

    return error
  }

  /**
   * 获取错误消息
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   * @returns {string} 错误消息
   */
  static getErrorMessage(error, context) {
    if (error?.message) {
      return `${context}失败: ${error.message}`
    }
    return `${context}失败`
  }

  /**
   * 处理聊天相关错误
   * @param {Error} error - 错误对象
   * @param {string} operation - 操作类型
   */
  static handleChatError(error, operation) {
    const contextMap = {
      send: '发送消息',
      create: '创建对话',
      delete: '删除对话',
      archive: '归档对话',
      rename: '重命名对话',
      load: '加载对话',
      stream: '流式处理',
      promote: '转为引导',
      cancel: '取消排队输入',
      continue_queue: '继续队列'
    }

    const context = contextMap[operation] || operation
    return this.handleError(error, context)
  }

  /**
   * 处理验证错误
   * @param {string} message - 验证错误消息
   */
  static handleValidationError(message) {
    return this.handleError(new Error(message), '输入验证', {
      severity: 'warning',
      customMessage: message
    })
  }
}

/**
 * 快捷方法
 */
export const handleChatError = ErrorHandler.handleChatError.bind(ErrorHandler)
export const handleValidationError = ErrorHandler.handleValidationError.bind(ErrorHandler)

export default ErrorHandler
