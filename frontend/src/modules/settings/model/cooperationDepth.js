export const COOPERATION_DEPTH_KEY = 'max_cooperation_depth'
export const COOPERATION_CONCURRENCY_KEY = 'max_cooperation_concurrency'
export const MAX_COOPERATION_CONCURRENCY = 20

/** 只接受完整非负整数；清空或非法输入不改变已保存值。 */
export function applyCooperationDepth(value, update) {
  if (Number.isInteger(value) && value >= 0) update(value)
}

/** 只接受配置范围内的整数；清空或非法输入不改变已保存值。 */
export function applyCooperationConcurrency(value, update) {
  if (Number.isInteger(value) && value >= 1 && value <= MAX_COOPERATION_CONCURRENCY) update(value)
}
