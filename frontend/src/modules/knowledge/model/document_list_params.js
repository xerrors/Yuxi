const SORT_FIELDS = new Set(['filename', 'content_amount', 'status', 'created_at'])
const SORT_ORDERS = new Set(['ascend', 'descend'])

/** 把表格 sorter 收成服务端字段和方向；取消或非法排序回到默认。 */
export const normalizeDocumentSort = (sorter) => {
  const columns = Array.isArray(sorter) ? sorter : [sorter]
  const active = columns.find((column) => SORT_ORDERS.has(column?.order))
  const sortBy = active?.columnKey || active?.field || null
  const sortOrder = active?.order || null
  if (!SORT_FIELDS.has(sortBy) || !SORT_ORDERS.has(sortOrder)) {
    return { sortBy: null, sortOrder: null }
  }
  return { sortBy, sortOrder }
}

/** 组装文件列表查询。未选择排序时不传 sort，避免改变服务端默认顺序。 */
export const buildDocumentListParams = ({
  page,
  pageSize,
  status,
  recursive,
  parentId,
  sortBy = null,
  sortOrder = null
}) => {
  const params = {
    page,
    page_size: pageSize,
    status,
    recursive
  }
  if (!recursive && parentId) {
    params.parent_id = parentId
  }
  if (sortBy && sortOrder) {
    params.sort_by = sortBy
    params.sort_order = sortOrder
  }
  return params
}
