export const createSearchConfigSnapshot = (queryParams, meta) => {
  const snapshot = {}
  for (const param of queryParams) {
    snapshot[param.key] = meta[param.key]
  }
  return snapshot
}

export const searchConfigChanged = (currentConfig, initialConfig) =>
  JSON.stringify(currentConfig) !== JSON.stringify(initialConfig)

const matchesCondition = (condition, meta) =>
  Object.entries(condition).every(([key, expectedValue]) => meta[key] === expectedValue)

export const isSearchParamVisible = (param, meta) => {
  const dependency = param.depend_on
  if (dependency?.length >= 2) {
    const [key, expectedValue] = dependency
    if (meta[key] !== expectedValue) return false
  }

  const visibleWhen = param.visible_when
  if (!visibleWhen) return true

  if (visibleWhen.any && !visibleWhen.any.some((condition) => matchesCondition(condition, meta))) {
    return false
  }
  if (visibleWhen.all && !visibleWhen.all.every((condition) => matchesCondition(condition, meta))) {
    return false
  }
  return true
}
