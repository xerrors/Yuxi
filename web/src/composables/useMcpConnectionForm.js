import { computed, reactive, shallowRef, toValue } from 'vue'
import {
  buildMcpCredentialFromForm,
  parseMcpJsonText,
  validateMcpCredentialFields
} from '@/utils/mcpConnectionUtils'

export function useMcpConnectionForm({
  secretFields,
  defaultScopeType = 'user',
  getDefaultScopeType,
  onError
} = {}) {
  const isOpen = shallowRef(false)
  const submitting = shallowRef(false)
  const editingConnectionId = shallowRef(null)
  const form = reactive({
    scopeType: resolveDefaultScopeType(),
    scopeId: '',
    displayName: '',
    externalSubject: '',
    credentialText: '',
    secretValues: {},
    metaText: ''
  })

  const isEditing = computed(() => editingConnectionId.value !== null)

  function resolveSecretFields() {
    return toValue(secretFields) || []
  }

  function resolveDefaultScopeType() {
    if (typeof getDefaultScopeType === 'function') {
      return getDefaultScopeType()
    }
    return toValue(defaultScopeType) || 'user'
  }

  function createEmptySecretValues() {
    return Object.fromEntries(resolveSecretFields().map((fieldName) => [fieldName, '']))
  }

  function resetForm(overrides = {}) {
    editingConnectionId.value = null
    Object.assign(form, {
      scopeType: resolveDefaultScopeType(),
      scopeId: '',
      displayName: '',
      externalSubject: '',
      credentialText: '',
      secretValues: createEmptySecretValues(),
      metaText: '',
      ...overrides
    })
  }

  function openCreateForm(overrides = {}) {
    resetForm(overrides)
    isOpen.value = true
  }

  function startEditForm(connection, overrides = {}) {
    if (!connection) return
    editingConnectionId.value = connection.id
    Object.assign(form, {
      scopeType: connection.scope_type || resolveDefaultScopeType(),
      scopeId: connection.scope_id || '',
      displayName: connection.display_name || '',
      externalSubject: connection.external_subject || '',
      credentialText: '',
      secretValues: createEmptySecretValues(),
      metaText: connection.meta_json ? JSON.stringify(connection.meta_json, null, 2) : '',
      ...overrides
    })
    isOpen.value = true
  }

  function closeForm() {
    isOpen.value = false
    resetForm()
  }

  function parseJsonText(text, label, options = {}) {
    return parseMcpJsonText(text, label, { ...options, onError })
  }

  function validateCredential() {
    return validateMcpCredentialFields({
      isEditing: isEditing.value,
      secretFields: resolveSecretFields(),
      secretValues: form.secretValues,
      credentialText: form.credentialText,
      onError
    })
  }

  function buildCredential({
    isEditing: credentialIsEditing = isEditing.value,
    emptyCreateValue = null,
    rawLabel = '长期凭据'
  } = {}) {
    return buildMcpCredentialFromForm({
      credentialText: form.credentialText,
      secretValues: form.secretValues,
      isEditing: credentialIsEditing,
      emptyCreateValue,
      rawLabel,
      onError
    })
  }

  function buildBasePayload(metaLabel = '连接元数据') {
    const metaJson = parseJsonText(form.metaText, metaLabel)
    if (metaJson === undefined) return undefined

    return {
      display_name: form.displayName || null,
      external_subject: form.externalSubject || null,
      meta_json: metaJson
    }
  }

  resetForm()

  return {
    isOpen,
    submitting,
    editingConnectionId,
    form,
    isEditing,
    createEmptySecretValues,
    resetForm,
    openCreateForm,
    startEditForm,
    closeForm,
    parseJsonText,
    validateCredential,
    buildCredential,
    buildBasePayload
  }
}
