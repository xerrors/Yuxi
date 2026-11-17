import { apiGet, apiPost, apiPut, apiDelete } from './base'

const BASE_URL = '/api/system/skills'
const USER_BASE_URL = '/api/skills'

export const listSkills = async () => {
  return apiGet(BASE_URL)
}

export const getSkillDetail = (slug) => apiGet(`${BASE_URL}/${encodeURIComponent(slug)}`)
export const getSkillContent = (slug) => apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/content`)
export const saveSkillContent = (slug, payload) =>
  apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/content`, payload)

export const listSkillCards = async () => apiGet(USER_BASE_URL)

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

export const getSkillFile = async (slug, path) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const getPersonalSkillFile = async (slug, path) => {
  return apiGet(
    `${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const updateSkillShareConfig = async (slug, shareConfig) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/share-config`, {
    share_config: shareConfig
  })
}

export const updateSkillEnabled = async (slug, enabled) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/enabled`, { enabled })
}

export const exportSkill = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/export`, {}, true, 'blob')
}

export const listSkillVersions = (slug) =>
  apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/versions`)

export const releaseSkillVersion = (slug, expectedRevision) =>
  apiPost(`${BASE_URL}/${encodeURIComponent(slug)}/versions`, { expected_revision: expectedRevision })

export const restoreSkillVersion = (slug, version, expectedRevision) =>
  apiPost(
    `${BASE_URL}/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}/restore`,
    { expected_revision: expectedRevision }
  )

export const deleteSkillVersion = (slug, version) =>
  apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}`)

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
  getSkillContent,
  saveSkillContent,
  listSkillVersions,
  releaseSkillVersion,
  restoreSkillVersion,
  deleteSkillVersion,
  getSkillDetail,
  listSkills,
  listSkillCards,
  prepareSkillUpload,
  listRemoteSkills,
  prepareRemoteSkills,
  searchRemoteSkills,
  confirmSkillInstallDraft,
  confirmPersonalSkillInstallDraft,
  discardSkillInstallDraft,
  getSkillFile,
  getPersonalSkillFile,
  updateSkillShareConfig,
  updateSkillEnabled,
  exportSkill,
  deleteSkill,
  deletePersonalSkill,
  deleteSkillsBatch
}

export default skillApi
