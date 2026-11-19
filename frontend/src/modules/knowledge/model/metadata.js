export const metadataTypeNames = {
  text: '文本',
  number: '数字',
  boolean: '是 / 否',
  date: '日期',
  select: '单选',
  multi_select: '多选'
}

export const metadataOperatorNames = {
  eq: '等于',
  ne: '不等于',
  gt: '大于 / 晚于',
  gte: '不小于 / 不早于',
  lt: '小于 / 早于',
  lte: '不大于 / 不晚于',
  contains_any: '包含任一选项',
  contains_all: '包含全部选项',
  exists: '已设置',
  not_exists: '未设置'
}

/** 按明确操作构造部分更新，未选择的字段不进入请求。 */
export function buildAttributePatch(fields, operations, values) {
  const patch = { set: {}, unset: [] }
  for (const field of fields) {
    const operation = operations[field.key]
    if (operation === 'unset') patch.unset.push(field.key)
    if (operation !== 'set') continue
    const value = values[field.key]
    if (
      value === undefined ||
      value === null ||
      value === '' ||
      (Array.isArray(value) && !value.length)
    ) {
      throw new Error(`请填写“${field.name}”，或选择清除`)
    }
    patch.set[field.key] = value
  }
  return patch
}

/** 提交前检查条件是否填完，最终字段与类型校验由后端执行。 */
export function validateMetadataFilter(filter, fields) {
  if (!filter) return null
  if (['and', 'or'].includes(filter.operator)) {
    if (!filter.conditions?.length) throw new Error('筛选分组至少需要一个条件')
    return {
      operator: filter.operator,
      conditions: filter.conditions.map((item) => validateMetadataFilter(item, fields))
    }
  }
  const field = fields.find((item) => item.key === filter.field)
  if (!field) throw new Error('请选择有效的筛选字段')
  if (!field.operators.includes(filter.operator)) throw new Error(`请选择“${field.name}”支持的条件`)
  if (['exists', 'not_exists'].includes(filter.operator))
    return { field: field.key, operator: filter.operator }
  const value = filter.value
  if (
    value === undefined ||
    value === null ||
    value === '' ||
    (Array.isArray(value) && !value.length)
  ) {
    throw new Error(`请填写“${field.name}”的筛选值`)
  }
  return { field: field.key, operator: filter.operator, value }
}

/** 将实际执行条件转换为字段名称，供结果区核对。 */
export function describeMetadataFilter(filter, fields) {
  if (!filter) return '未使用属性筛选'
  if (['and', 'or'].includes(filter.operator)) {
    return `（${filter.conditions.map((item) => describeMetadataFilter(item, fields)).join(filter.operator === 'and' ? ' 且 ' : ' 或 ')}）`
  }
  const field = fields.find((item) => item.key === filter.field)
  const value = Array.isArray(filter.value)
    ? filter.value.join('、')
    : typeof filter.value === 'boolean'
      ? filter.value
        ? '是'
        : '否'
      : filter.value
  return `${field?.name || filter.field} ${metadataOperatorNames[filter.operator] || filter.operator}${value === undefined || value === null ? '' : ` ${value}`}`
}
