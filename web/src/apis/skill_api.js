import { apiAdminGet, apiAdminPost, apiAdminPut, apiAdminDelete } from './base'

const BASE_URL = '/api/system/skills'

export const listSkills = async () => {
  return apiAdminGet(BASE_URL)
}

export const importSkillZip = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return apiAdminPost(`${BASE_URL}/import`, formData)
}

export const listRemoteSkills = async (source) => {
  return apiAdminPost(`${BASE_URL}/remote/list`, { source })
}

export const installRemoteSkill = async (payload) => {
  return apiAdminPost(`${BASE_URL}/remote/install`, payload)
}

export const installRemoteSkillsBatch = async (payload) => {
  return apiAdminPost(`${BASE_URL}/remote/install-batch`, payload)
}

export const getSkillDependencyOptions = async () => {
  return apiAdminGet(`${BASE_URL}/dependency-options`)
}

export const listBuiltinSkills = async () => {
  return apiAdminGet(`${BASE_URL}/builtin`)
}

export const installBuiltinSkill = async (slug) => {
  return apiAdminPost(`${BASE_URL}/builtin/${encodeURIComponent(slug)}/install`)
}

export const updateBuiltinSkill = async (slug, force = false) => {
  return apiAdminPost(`${BASE_URL}/builtin/${encodeURIComponent(slug)}/update`, { force })
}

export const getSkillTree = async (slug) => {
  return apiAdminGet(`${BASE_URL}/${encodeURIComponent(slug)}/tree`)
}

export const getSkillFile = async (slug, path) => {
  return apiAdminGet(
    `${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const createSkillFile = async (slug, payload) => {
  return apiAdminPost(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillFile = async (slug, payload) => {
  return apiAdminPut(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillDependencies = async (slug, payload) => {
  return apiAdminPut(`${BASE_URL}/${encodeURIComponent(slug)}/dependencies`, payload)
}

export const deleteSkillFile = async (slug, path) => {
  return apiAdminDelete(
    `${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const exportSkill = async (slug) => {
  return apiAdminGet(`${BASE_URL}/${encodeURIComponent(slug)}/export`, {}, 'blob')
}

export const deleteSkill = async (slug) => {
  return apiAdminDelete(`${BASE_URL}/${encodeURIComponent(slug)}`)
}

export const skillApi = {
  listSkills,
  importSkillZip,
  listRemoteSkills,
  installRemoteSkill,
  installRemoteSkillsBatch,
  getSkillDependencyOptions,
  listBuiltinSkills,
  installBuiltinSkill,
  updateBuiltinSkill,
  getSkillTree,
  getSkillFile,
  createSkillFile,
  updateSkillFile,
  updateSkillDependencies,
  deleteSkillFile,
  exportSkill,
  deleteSkill
}

export default skillApi
