import { normalizeArtifacts } from './artifacts.js'

/** 交付路径倒序展示为文件项，保持原始字符串路径的稳定身份。 */
export function buildArtifactItems(artifacts) {
  return normalizeArtifacts(artifacts)
    .reverse()
    .map((path) => ({ path, name: path.split('/').pop() || path }))
}

/** 最多展示四项；超过时折叠其余项，展开后展示全部。 */
export function visibleArtifactItems(items, expanded) {
  return expanded || items.length <= 4 ? items : items.slice(0, 4)
}
