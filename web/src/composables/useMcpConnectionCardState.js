import { toValue } from 'vue'
import { formatFullDateTime } from '@/utils/time'
import {
  MCP_CONNECTION_STATUS_LABELS, canRunMcpConnectionAction, canToggleMcpConnectionStatus,
  formatMcpConnectionLastInfo, getMcpConnectionActionTooltip, getMcpConnectionIssue,
  getMcpConnectionStatusSwitchLabel, getMcpConnectionStatusToggleTooltip
} from '@/utils/mcpConnectionUtils'
export function useMcpConnectionCardState({
  isScopeMatched = () => true,
  isCredentialMissing = () => false,
  includeScopeMismatch = true,
  authBindingScopeLabel = '未限定',
  missingCredentialsDescription,
  reauthRequiredDescription
} = {}) {
  const actionOptions = () => ({
    isScopeMatched,
    isCredentialMissing,
    authBindingScopeLabel: toValue(authBindingScopeLabel) || '未限定'
  })
  const getStatusLabel = (status) => MCP_CONNECTION_STATUS_LABELS[status] || status || '未知状态'
  const actionTooltip = (connection, enabledText, scopeMismatchText) =>
    getMcpConnectionActionTooltip(connection, enabledText, scopeMismatchText, actionOptions())

  return {
    canToggleConnectionStatus: (connection) => canToggleMcpConnectionStatus(connection, actionOptions()),
    canTestConnection: (connection) => canRunMcpConnectionAction(connection, actionOptions()),
    canReauthorizeConnection: (connection) => canRunMcpConnectionAction(connection, actionOptions()),
    getConnectionStatusSwitchLabel: (connection) => getMcpConnectionStatusSwitchLabel(connection, getStatusLabel),
    getConnectionStatusToggleTooltip: (connection) => getMcpConnectionStatusToggleTooltip(connection, actionOptions()),
    getConnectionTestTooltip: (connection) =>
      actionTooltip(
        connection,
        '测试连接',
        `该连接未生效，当前 MCP 使用${actionOptions().authBindingScopeLabel}`
      ),
    getConnectionReauthorizeTooltip: (connection) =>
      actionTooltip(
        connection,
        '重置授权并重新激活',
        `该连接未生效，不能重连；当前 MCP 使用${actionOptions().authBindingScopeLabel}`
      ),
    getConnectionIssue: (connection) =>
      getMcpConnectionIssue(connection, {
        includeScopeMismatch: toValue(includeScopeMismatch),
        isScopeMatched,
        isCredentialMissing,
        authBindingScopeLabel: actionOptions().authBindingScopeLabel,
        missingCredentialsDescription,
        reauthRequiredDescription
      }),
    getConnectionLastInfo: (connection) => formatMcpConnectionLastInfo(connection, formatFullDateTime)
  }
}
