export const upsertAgentPanelSection = (sections, section) => {
  const current = Array.isArray(sections) ? sections : []
  const index = current.findIndex((item) => item.key === section.key)
  if (index < 0) return [...current, section]
  return current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...section } : item))
}

export const closeAgentPanelSection = (sections, activeKey, closingKey) => {
  const closingIndex = sections.findIndex((section) => section.key === closingKey)
  if (closingIndex < 0) return { sections, activeKey }

  const nextSections = sections.filter((section) => section.key !== closingKey)
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

export const FILE_TREE_SECTION = { key: 'file-tree', type: 'file-tree', title: '文件' }
export const MESSAGE_DEBUG_SECTION = { key: 'message-debug', type: 'message-debug', title: '调试' }
