/** 按 JSON 语法格式化树节点中的标量值。 */
export function formatJsonScalar(value) {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (typeof value === 'string') return JSON.stringify(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

/** 按 JSON 语法格式化对象键名。 */
export function formatJsonKey(value) {
  return JSON.stringify(String(value))
}
