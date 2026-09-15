export function assertKnowledgeUploadSize(file, limits) {
  const maximum = limits?.effective_upload_max_bytes
  if (!Number.isSafeInteger(maximum) || maximum <= 0) {
    throw new Error('文档上传限制暂不可用，请刷新后重试')
  }
  if (file.size > maximum) {
    throw new Error(`文件超过当前 ${maximum / 1024 / 1024} MiB 上传上限`)
  }
}
