/**
 * 知识库文件名的扩展名判据。
 *
 * 前端预校验只负责「让用户看到具体原因」——`apis/base.js` 对所有 400 一律返回
 * 「请求参数错误」，服务端写好的文案到不了用户眼前；判据必须与后端逐字一致，
 * 否则会出现「前端拒绝、服务端允许」的假拒绝。服务端仍是唯一权威。
 */

/**
 * 取扩展名，语义等于 Python 的 os.path.splitext(name)[1]。
 *
 * 前导点不算扩展名（".env" 无后缀），末尾点算（"x." 的后缀是 "."），
 * 整个点之前全由点组成时也算无后缀（"..env" 无后缀）。
 * 不能简写成 `name.slice(name.lastIndexOf('.'))`：那会把 "..env" 判成有后缀，
 * 与后端 `yuxi.knowledge.base._filename_extensions` 的 splitext 一侧漂移。
 */
export const filenameExtension = (name) => {
  const dot = name.lastIndexOf('.')
  return dot > 0 && [...name.slice(0, dot)].some((ch) => ch !== '.')
    ? name.slice(dot).toLowerCase()
    : ''
}

/** 与后端 rename_file 的「文件名不能只由点组成」判据一致。 */
export const isAllDotsName = (name) => /^\.+$/.test(name)

/**
 * 文件行是否允许重命名。
 *
 * 虚拟目录视图里行名被裁掉了目录前缀，根视图里历史虚拟目录的文件名本身带前缀，
 * 两种情况改名都会把文件搬出它所在的目录，因此不提供入口。
 */
export const canRenameFileRecord = ({
  filename,
  isVirtualPathView = false,
  readonly = false
} = {}) => {
  const name = String(filename || '')
  if (!name || readonly || isVirtualPathView) return false
  return !name.includes('/')
}
