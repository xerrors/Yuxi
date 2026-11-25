import { threadApi } from '@/apis/agent_api'
import { getViewerFileContent } from '@/apis/viewer_filesystem'
import { getWorkspaceFileContent, getWorkspaceKnowledgeFileContent } from '@/apis/workspace_api'

/**
 * 用现有鉴权请求拉取 docx/pptx 的 LibreOffice PDF。
 * 不把带凭证的 URL 交给预览组件。
 */
export const loadOfficePdfFallback = (file) => {
  const kbId = file?.kb_id || file?.kbId
  const fileId = file?.file_id || file?.fileId
  if (kbId && fileId) {
    return getWorkspaceKnowledgeFileContent(kbId, fileId, { officeRender: 'pdf' })
  }
  if (file?.workspace && file?.path) {
    return getWorkspaceFileContent(file.path, { officeRender: 'pdf' })
  }
  if (file?.workdir && file?.threadId && file?.path) {
    return getViewerFileContent(file.threadId, file.path, { officeRender: 'pdf' })
  }
  if (file?.threadId && file?.path) {
    return threadApi.previewThreadArtifact(file.threadId, file.path, { officeRender: 'pdf' })
  }
  if (file?.path) return getWorkspaceFileContent(file.path, { officeRender: 'pdf' })
  return Promise.resolve(null)
}
