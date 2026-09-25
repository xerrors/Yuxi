export const AUTO_PROJECT_ID = '__auto__'
export const PROJECT_NAME_MAX_LENGTH = 100

/**
 * 新建文件夹成功后补齐项目名称：项目名称为空时用文件夹名填充，
 * 已有输入保持用户原值不被覆盖，与“文件夹名预填项目名称”方向互补。
 */
export const fillProjectNameFromFolder = (projectName, folderName) => {
  const current = typeof projectName === 'string' ? projectName : ''
  const folder = typeof folderName === 'string' ? folderName.trim() : ''
  if (current.trim() || !folder) return current
  return folder.slice(0, PROJECT_NAME_MAX_LENGTH)
}

export const filterProjects = (projects, query = '') => {
  const keyword = String(query).trim().toLocaleLowerCase()
  if (!keyword) return projects
  return projects.filter((project) => project.name.toLocaleLowerCase().includes(keyword))
}

export const formatRelativeTime = (value, now = Date.now()) => {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return ''

  const elapsed = Math.max(0, Number(now) - timestamp)
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  if (elapsed < minute) return '刚刚'
  if (elapsed < hour) return `${Math.floor(elapsed / minute)}分钟前`
  if (elapsed < day) return `${Math.floor(elapsed / hour)}小时前`
  if (elapsed < 30 * day) return `${Math.floor(elapsed / day)}天前`
  if (elapsed < 365 * day) return `${Math.floor(elapsed / (30 * day))}个月前`
  return `${Math.floor(elapsed / (365 * day))}年前`
}
