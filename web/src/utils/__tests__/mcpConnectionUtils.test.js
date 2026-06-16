import assert from 'node:assert/strict'

import {
  buildMcpCredentialFromForm,
  canRunMcpConnectionAction,
  canToggleMcpConnectionStatus,
  formatMcpConnectionLastInfo,
  getMcpConnectionActionTooltip,
  getMcpConnectionIssue,
  getMcpSecretFieldLabel,
  parseMcpJsonText,
  validateMcpCredentialFields
} from '../mcpConnectionUtils.js'

const errors = []
const onError = errors.push.bind(errors)

assert.equal(getMcpSecretFieldLabel('client_id'), 'Client ID')
assert.equal(getMcpSecretFieldLabel('custom_secret'), 'custom_secret')
assert.deepEqual(parseMcpJsonText('{"ok": true}', '测试', { onError }), { ok: true })
assert.equal(parseMcpJsonText('raw-token', '测试', { allowRawString: true }), 'raw-token')
assert.equal(parseMcpJsonText('{bad', '测试', { onError }), undefined)
assert.deepEqual(errors, ['测试 JSON 格式错误'])

assert.deepEqual(
  buildMcpCredentialFromForm({
    secretValues: { client_id: ' cid ', 'nested.token': 'token' }
  }),
  { secrets: { client_id: 'cid', nested: { token: 'token' } } }
)
assert.equal(buildMcpCredentialFromForm({ isEditing: true }), undefined)
assert.equal(buildMcpCredentialFromForm({ emptyCreateValue: null }), null)

const missingErrors = []
assert.equal(
  validateMcpCredentialFields({
    secretFields: ['client_id'],
    secretValues: {},
    onError: missingErrors.push.bind(missingErrors)
  }),
  false
)
assert.deepEqual(missingErrors, ['请填写凭据字段：client_id'])
assert.equal(validateMcpCredentialFields({ secretFields: ['client_id'], credentialText: 'raw' }), true)
assert.equal(
  canToggleMcpConnectionStatus(
    { status: 'disabled', has_credentials: true },
    { isScopeMatched: () => false }
  ),
  false
)
assert.equal(canRunMcpConnectionAction({ has_credentials: true }, { isCredentialMissing: () => false }), true)
assert.equal(
  getMcpConnectionActionTooltip(
    { has_credentials: true },
    '测试连接',
    '范围不匹配',
    { isScopeMatched: () => false }
  ),
  '范围不匹配'
)

assert.equal(
  getMcpConnectionIssue({ has_credentials: false, status: 'active' }, { isCredentialMissing: () => true }).key,
  'missing_credentials'
)
assert.equal(
  getMcpConnectionIssue({ has_credentials: true, status: 'active' }, { isScopeMatched: () => false }).key,
  'scope_mismatch'
)
assert.equal(
  getMcpConnectionIssue({
    has_credentials: true,
    status: 'invalid',
    meta_json: { last_error: { message: 'token expired' } }
  }).description,
  'token expired'
)
assert.equal(
  formatMcpConnectionLastInfo(
    { meta_json: { last_success_at: '2026-01-01T00:00:00Z' } },
    (value) => `formatted:${value}`
  ),
  '最近成功 formatted:2026-01-01T00:00:00Z'
)

console.log('mcpConnectionUtils: all assertions passed')
