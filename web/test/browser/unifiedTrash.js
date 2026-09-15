// Real page/components with synthetic API responses; never connects to a deployed API.
// Run Vite from web/, open /test/browser/unifiedTrash.html; add ?dark, ?empty or ?error.
import { createApp, h } from 'vue'
import Antd, { ConfigProvider, theme } from 'ant-design-vue'
import GlobalTrashView from '../../src/views/GlobalTrashView.vue'
import { databaseApi, documentApi } from '../../src/apis/knowledge_api'
import { personalTrashApi } from '../../src/apis/personal_trash_api'
import '../../src/assets/css/main.css'

const options = new URLSearchParams(location.search)
const dark = options.has('dark')
document.documentElement.classList.toggle('dark', dark)
const future = new Date(Date.now() + 29 * 86400000).toISOString()
const past = new Date(Date.now() - 86400000).toISOString()
let files = options.has('empty') ? [] : [
  { id: 'personal', name: '合同资料', kind: 'workspace', paths: ['files/合同资料'], purge_after: future, state: 'trashed' },
  { id: 'attachment', name: '会话附件.pdf', kind: 'attachment', paths: ['attachments/fixture/会话附件.pdf'], purge_after: future, state: 'trashed' },
  { id: 'expired', name: '到期资料.pdf', kind: 'workspace', paths: ['files/到期资料.pdf'], purge_after: past, state: 'trashed' }
]
let documents = options.has('empty') ? [] : [
  { file_id: 'document', filename: '企业合同.pdf', deleted_at: past, purge_after: future, status: 'trashed' },
  { file_id: 'retry', filename: '等待重试.pdf', deleted_at: past, purge_after: past, status: 'purge_failed', error_message: '自动清理失败，系统将重试' }
]
const failIfRequested = () => { if (options.has('error')) throw new Error('合成加载失败，请重试') }
databaseApi.getTrashDatabases = async () => {
  failIfRequested()
  return { databases: [{ kb_id: 'fixture', name: '演示企业知识库' }] }
}
documentApi.getTrash = async () => {
  failIfRequested()
  return { items: documents, total: documents.length }
}
documentApi.restoreDocument = async (_kb, id) => {
  documents = documents.filter((row) => row.file_id !== id)
  return { restored_count: 1 }
}
personalTrashApi.list = async () => {
  failIfRequested()
  return { items: files, total: files.length }
}
personalTrashApi.restore = async (id) => { files = files.filter((row) => row.id !== id) }
personalTrashApi.retry = async () => ({})
createApp({
  render() {
    return h(ConfigProvider, { theme: { algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm } }, () => [
      h('p', { style: 'margin: 8px 24px' }, '浏览器验证：全部为合成资料，不连接真实服务器'),
      h(GlobalTrashView)
    ])
  }
}).use(Antd).mount('#app')
