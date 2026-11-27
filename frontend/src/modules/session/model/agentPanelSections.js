export const upsertAgentPanelSection = (sections, section) => {
  const current = Array.isArray(sections) ? sections : []
  const index = current.findIndex((item) => item.key === section.key)
  if (index < 0) return [...current, section]
  return current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...section } : item))
}

export const closeAgentPanelSection = (sections, activeKey, closingKey) => {
  const closingIndex = sections.findIndex((section) => section.key === closingKey)
  if (closingIndex < 0) return { sections, activeKey }

  const nextSections = sections.length === 1
    ? [START_SECTION]
    : sections.filter((section) => section.key !== closingKey)
  if (activeKey !== closingKey) return { sections: nextSections, activeKey }
  const nextActive = nextSections[Math.min(closingIndex, nextSections.length - 1)]
  return { sections: nextSections, activeKey: nextActive?.key || '' }
}

export const shouldPollAgentPanelFilesystem = ({
  panelOpen,
  pageVisible,
  streaming,
  activeSection,
  activePreview
}) => {
  if (!panelOpen || !pageVisible || !streaming) return false
  if (activeSection?.type === 'file-tree') return true
  return activeSection?.type === 'file' && activePreview?.workdir === true
}

export const START_SECTION = { key: 'start', type: 'start', title: '开始' }
export const FILE_TREE_SECTION = { key: 'file-tree', type: 'file-tree', title: '工作区文件' }
export const WORKSPACE_TREE_SECTION = { key: 'workspace-tree', type: 'workspace-tree', title: '个人空间文件' }
export const COOPERATION_SECTION = { key: 'cooperation', type: 'cooperation', title: '协作详情' }
export const MESSAGE_DEBUG_SECTION = { key: 'message-debug', type: 'message-debug', title: '调试' }
// 来源面板复用同一标签，turnId 与回退数据由打开方写入。
export const TURN_SOURCES_SECTION = { key: 'turn-sources', type: 'turn-sources', title: '来源' }
