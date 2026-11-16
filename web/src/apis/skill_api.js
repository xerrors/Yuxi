import { apiGet, apiPost, apiPut, apiDelete, apiAdminGet, apiAdminPost } from './base'

const BASE_URL = '/api/system/skills'
const USER_BASE_URL = '/api/skills'

export const listSkills = async () => {
  return apiGet(BASE_URL)
}

export const listSkillCards = async ({ refreshPersonal = false } = {}) => {
  const query = refreshPersonal ? '?refresh_personal=true' : ''
  return apiGet(`${USER_BASE_URL}${query}`)
}

export const listAccessibleSkills = async () => {
  return apiGet(`${USER_BASE_URL}/accessible`)
}

export const prepareSkillUpload = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return apiPost(`${USER_BASE_URL}/import/prepare`, formData)
}

export const listRemoteSkills = async (source) => {
  return apiPost(`${USER_BASE_URL}/remote/list`, { source })
}

export const prepareRemoteSkills = async (payload) => {
  return apiPost(`${USER_BASE_URL}/remote/prepare`, payload)
}

export const searchRemoteSkills = async (query) => {
  return apiPost(`${USER_BASE_URL}/remote/search`, { query })
}

export const confirmSkillInstallDraft = async (draftId, shareConfig, slugs) => {
  return apiPost(`${USER_BASE_URL}/install-drafts/${encodeURIComponent(draftId)}/confirm`, {
    share_config: shareConfig,
    slugs
  })
}

export const confirmPersonalSkillInstallDraft = async (draftId, slugs) => {
  return apiPost(
    `${USER_BASE_URL}/personal/install-drafts/${encodeURIComponent(draftId)}/confirm`,
    {
      slugs
    }
  )
}

export const discardSkillInstallDraft = async (draftId) => {
  return apiDelete(`${USER_BASE_URL}/install-drafts/${encodeURIComponent(draftId)}`)
}

export const getSkillDependencyOptions = async (slug) => {
  const query = slug ? `?slug=${encodeURIComponent(slug)}` : ''
  return apiGet(`${BASE_URL}/dependency-options${query}`)
}

export const listBuiltinSkills = async () => {
  return apiAdminGet(`${BASE_URL}/builtin`)
}

export const syncBuiltinSkills = async () => {
  return apiAdminPost(`${BASE_URL}/builtin/sync`)
}

export const getSkillTree = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/tree`)
}

export const getSkillFile = async (slug, path) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const getPersonalSkillFile = async (slug, path) => {
  return apiGet(
    `${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const createSkillFile = async (slug, payload) => {
  return apiPost(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillFile = async (slug, payload) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillDependencies = async (slug, payload) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/dependencies`, payload)
}

export const updateSkillShareConfig = async (slug, shareConfig) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/share-config`, {
    share_config: shareConfig
  })
}

export const updateSkillEnabled = async (slug, enabled) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/enabled`, { enabled })
}

export const deleteSkillFile = async (slug, path) => {
  return apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const exportSkill = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/export`, {}, true, 'blob')
}

export const deleteSkill = async (slug) => {
  return apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}`)
}

export const deletePersonalSkill = async (slug) => {
  return apiDelete(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}`)
}

export const deleteSkillsBatch = async (slugs) => {
  return apiPost(`${BASE_URL}/delete-batch`, { slugs })
}

export const skillApi = {
  listSkills,
  listSkillCards,
  listAccessibleSkills,
  prepareSkillUpload,
  listRemoteSkills,
  prepareRemoteSkills,
  searchRemoteSkills,
  confirmSkillInstallDraft,
  confirmPersonalSkillInstallDraft,
  discardSkillInstallDraft,
  getSkillDependencyOptions,
  listBuiltinSkills,
  syncBuiltinSkills,
  getSkillTree,
  getSkillFile,
  getPersonalSkillFile,
  createSkillFile,
  updateSkillFile,
  updateSkillDependencies,
  updateSkillShareConfig,
  updateSkillEnabled,
  deleteSkillFile,
  exportSkill,
  deleteSkill,
  deletePersonalSkill,
  deleteSkillsBatch
}

export default skillApi
