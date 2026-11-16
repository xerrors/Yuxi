export const canMutateKnowledgeFiles = ({ readonly, locked, filtered, virtualPath }) =>
  !readonly && !locked && !filtered && !virtualPath

export const canDragKnowledgeFile = ({ enabled, record, breadcrumbs, files }) =>
  Boolean(
    enabled &&
    record &&
    !record.is_virtual_folder &&
    (breadcrumbs.slice(0, -1).some((item) => !item.dropDisabled) ||
      files.some(
        (file) => file.is_folder && !file.is_virtual_folder && file.file_id !== record.file_id
      ))
  )

export const canDropKnowledgeFileIntoFolder = (record, target) =>
  Boolean(
    record && target?.is_folder && !target.is_virtual_folder && target.file_id !== record.file_id
  )

export const canDropOnFileBreadcrumb = ({ enabled, item, index, count }) =>
  Boolean(enabled && item && !item.dropDisabled && index !== count - 1)
