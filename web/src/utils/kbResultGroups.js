/** 取片段的引用编号；后端未写入时返回 null，由调用方自行按位置编号。 */
export function getChunkCite(chunk) {
  const raw = chunk?.cite ?? chunk?.citation ?? chunk?.citation_index
  const parsed = Number.parseInt(String(raw ?? ''), 10)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

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
        chunks: [],
        cites: []
      })
    }
    const group = groups.get(key)
    group.chunks.push(item)

    const cite = getChunkCite(item)
    if (cite !== null) group.cites.push(cite)
  }

  const sorted = Array.from(groups.values()).sort((a, b) => a.filename.localeCompare(b.filename))
  for (const group of sorted) {
    group.cites.sort((a, b) => a - b)
  }
  return sorted
}
