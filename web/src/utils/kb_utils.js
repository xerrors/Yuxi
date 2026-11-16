import { h } from 'vue'
import { Database, DatabaseZap } from '@lucide/vue'

const ICON_BASE = 'https://registry.npmmirror.com/@lobehub/icons-static-svg/latest/files/icons'

const createBrandIcon = (url) => {
  const Icon = ({ size = 20 }) =>
    h('img', { src: url, style: { width: size + 'px', height: size + 'px' } })
  Icon.inheritAttrs = false
  return Icon
}

export const brandIcons = {
  dify: createBrandIcon(`${ICON_BASE}/dify-color.svg`),
  notion: createBrandIcon(`${ICON_BASE}/notion.svg`)
}

export const getKbTypeLabel = (type) => {
  const normalized = String(type || '').toLowerCase()
  const labels = {
    milvus: 'Yuxi',
    dify: 'Dify',
    notion: 'Notion'
  }
  return labels[normalized] || type
}

export const getKbTypeIcon = (type) => {
  const icons = {
    milvus: DatabaseZap,
    dify: brandIcons.dify,
    notion: brandIcons.notion
  }
  return icons[type] || Database
}

export const getKbTypeColor = (type) => {
  const colors = {
    milvus: 'blue',
    dify: 'gold',
    notion: 'purple'
  }
  return colors[type] || 'blue'
}

const READ_ONLY_KB_TYPES = new Set(['dify', 'notion'])

export const isReadOnlyDatabase = (database, kbTypes = {}) => {
  const kbType = (
    typeof database === 'string' ? database : database?.kb_type || 'milvus'
  ).toLowerCase()

  if (database?.supports_documents !== undefined) {
    return database.supports_documents === false
  }
  if (kbTypes[kbType]?.supports_documents !== undefined) {
    return kbTypes[kbType].supports_documents === false
  }
  return READ_ONLY_KB_TYPES.has(kbType)
}

export const kbUtils = {
  getKbTypeLabel,
  getKbTypeIcon,
  getKbTypeColor,
  isReadOnlyDatabase
}
