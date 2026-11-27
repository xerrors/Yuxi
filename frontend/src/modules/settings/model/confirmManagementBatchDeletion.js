import { message, Modal } from 'ant-design-vue'
import { useUserStore } from '@/modules/identity'

/** 一次确认后逐项删除，并用刷新后的列表呈现各项结果。 */
export function confirmManagementBatchDeletion({ ids, label, consequence, deleteById, refresh }) {
  if (!ids.length) return
  const userStore = useUserStore()
  const token = userStore.token
  const dialog = Modal.confirm({
    title: `确认批量删除${label}`,
    content: `确定删除选中的 ${ids.length} 个${label}吗？此操作不可撤销。${consequence}`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    autoFocusButton: 'cancel',
    async onOk() {
      dialog.update({ keyboard: false, cancelButtonProps: { disabled: true } })
      const failedIds = []
      for (const id of ids) {
        if (userStore.token !== token) return
        try {
          await deleteById(id)
        } catch {
          failedIds.push(id)
        }
      }
      if (userStore.token !== token) return
      await refresh(failedIds)
      if (userStore.token !== token) return

      const deletedCount = ids.length - failedIds.length
      if (failedIds.length === 0) {
        message.success(`已删除 ${deletedCount} 个${label}`)
      } else if (deletedCount === 0) {
        message.error(`${failedIds.length} 个${label}的删除结果未确认`)
      } else {
        message.warning(`已删除 ${deletedCount} 个${label}，${failedIds.length} 项结果未确认`)
      }
    }
  })
}
