import { shallowRef, toValue } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { mcpApi } from '@/apis/mcp_api'

export function useMcpConnectionActions({ serverName, reload, getConnectionTitle, onDeleted } = {}) {
  const actionLoading = shallowRef(null)

  function resolveServerName() {
    return typeof serverName === 'function' ? serverName() : toValue(serverName)
  }

  function getLoadingKey(connection, action) {
    return `${connection?.id || 'current'}:${action}`
  }

  function isActionLoading(connection, action) {
    return actionLoading.value === getLoadingKey(connection, action)
  }

  async function reloadConnections() {
    if (typeof reload === 'function') {
      await reload()
    }
  }

  async function updateConnectionStatus(connection, checked, { canToggle = () => true } = {}) {
    const currentServerName = resolveServerName()
    if (!currentServerName || !connection || !canToggle(connection)) return
    const nextStatus = checked ? 'active' : 'disabled'
    if (connection.status === nextStatus) return

    try {
      actionLoading.value = getLoadingKey(connection, 'status')
      const result = await mcpApi.updateMcpConnectionStatus(
        currentServerName,
        connection.id,
        nextStatus
      )
      if (result.success) {
        message.success(result.message || (checked ? '连接已启用' : '连接已停用'))
      } else {
        message.error(result.message || '状态更新失败')
      }
      await reloadConnections()
    } catch (err) {
      message.error(err.message || '状态更新失败')
      await reloadConnections()
    } finally {
      actionLoading.value = null
    }
  }

  async function testConnection(connection, { canTest = () => true } = {}) {
    const currentServerName = resolveServerName()
    if (!currentServerName || !connection || !canTest(connection)) return

    try {
      actionLoading.value = getLoadingKey(connection, 'test')
      const result = await mcpApi.testMcpConnection(currentServerName, connection.id)
      if (result.success === false) {
        message.error(result.message || '连接测试失败')
        return
      }
      message.success(result.message || '连接测试成功')
      await reloadConnections()
    } catch (err) {
      message.error(err.message || '连接测试失败')
    } finally {
      actionLoading.value = null
    }
  }

  async function reauthorizeConnection(connection, { canReauthorize = () => true } = {}) {
    const currentServerName = resolveServerName()
    if (!currentServerName || !connection || !canReauthorize(connection)) return

    try {
      actionLoading.value = getLoadingKey(connection, 'reauth')
      const result = await mcpApi.reauthorizeMcpConnection(currentServerName, connection.id)
      if (result.success === false) {
        message.error(result.message || '连接重置失败')
        return
      }
      message.success(result.message || '连接已重置')
      await reloadConnections()
    } catch (err) {
      message.error(err.message || '连接重置失败')
    } finally {
      actionLoading.value = null
    }
  }

  function deleteConnection(connection, options = {}) {
    const currentServerName = resolveServerName()
    if (!currentServerName || !connection) return
    const title = options.title || '确认删除连接'
    const titleText = getConnectionTitle?.(connection) || connection.display_name || connection.id

    Modal.confirm({
      title,
      content: options.content || `确定要删除连接 "${titleText}" 吗？`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      async onOk() {
        try {
          const result = await mcpApi.deleteMcpServerConnection(currentServerName, connection.id)
          if (result.success === false) {
            message.error(result.message || '连接删除失败')
            return
          }
          message.success(result.message || '连接已删除')
          await onDeleted?.(connection)
          await reloadConnections()
        } catch (err) {
          message.error(err.message || '连接删除失败')
        }
      }
    })
  }

  return {
    actionLoading,
    getLoadingKey,
    isActionLoading,
    updateConnectionStatus,
    testConnection,
    reauthorizeConnection,
    deleteConnection
  }
}
