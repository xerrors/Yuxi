// 模块公开模型与用例入口；UI 组件见 ./ui/**/*.vue。
export {
  WORKSPACE_UPLOAD_MAX_FILES,
  buildFolderUploadBatches,
  collectDragUploadRoots,
  collectFolderUploadEntries,
  traverseDirectoryEntry,
  validateFolderUploadEntries
} from './model/workspaceFolderUpload'
export { useFilePreviewSession } from './model/filePreviewSession'
export {
  filePreviewCacheKey, releaseFilePreviewCacheEntry, releaseFilePreviewCache, pruneFilePreviewCache,
  reloadPreviewAfterOrderedCacheEntryInvalidation, replacePreviewCacheEntryIfCurrent, settlePreviewCacheLoad
} from './model/filePreviewCache'
