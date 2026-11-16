/** 按知识库文件身份聚合检索片段。 */
export function groupKnowledgeChunks(chunks) {
  const groups = new Map()

  for (const item of chunks) {
    const filename = item?.metadata?.source || '未知来源'
    const kbId = item?.kb_id || ''
    const fileId = item?.file_id || ''
    const key = `${kbId}\u0000${fileId}\u0000${filename}`

    if (!groups.has(key)) {
      groups.set(key, {
        key,
        filename,
        kb_id: kbId,
        file_id: fileId,
        chunks: []
      })
    }
    groups.get(key).chunks.push(item)
  }

  return Array.from(groups.values()).sort((a, b) => a.filename.localeCompare(b.filename))
}
