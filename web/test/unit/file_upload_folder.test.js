import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('知识库详情页下拉菜单提供上传文件夹入口', () => {
  const source = readSource('../../src/views/DataBaseInfoView.vue')
  const menu = source.slice(source.indexOf('<Transition name="file-action-menu">'), source.indexOf('</Transition>'))

  assert.match(menu, /onUploadFolderAction/)
  assert.match(menu, /上传文件夹/)
  assert.match(source, /showAddFilesModal\(\{\s*isFolder:\s*true,\s*mode:\s*'folder'\s*\}\)/)
})

test('FileTable 创建文件夹成功后进入新文件夹，使其成为上传默认目标', () => {
  const source = readSource('../../src/components/FileTable.vue')
  const handler = source.slice(
    source.indexOf('const handleCreateFolder'),
    source.indexOf('const renameFolderModalVisible')
  )

  assert.match(handler, /const folder = await documentApi\.createFolder\(/)
  assert.match(handler, /await openFolder\(\{ file_id: folder\.file_id, filename: folder\.filename, is_folder: true \}\)/)
  assert.doesNotMatch(handler, /refreshAfterMutation/)
})

test('FileUploadModal 收集相对路径 source_paths 并过滤隐藏文件', () => {
  const source = readSource('../../src/components/FileUploadModal.vue')

  assert.match(source, /source_paths\[file_path\]\s*=\s*relativePath/)
  assert.match(source, /params\.source_paths\s*=\s*source_paths/)
  assert.match(source, /const isHiddenPath/)
  assert.match(source, /Upload\.LIST_IGNORE/)
})

test('FileUploadModal 文件数量与进度统计仅计算受支持且非隐藏的文件', () => {
  const source = readSource('../../src/components/FileUploadModal.vue')

  assert.match(source, /const validFileList\s*=\s*computed/)
  assert.match(source, /const totalUploadCount\s*=\s*computed\(\(\)\s*=>\s*validFileList\.value\.length\)/)
  assert.match(source, /for\s*\(\s*const file of validFileList\.value\s*\)/)
  assert.match(source, /const isSupportedUploadFile\s*=\s*\(file\)/)
})

test('documentApi.addDocuments 能够将 source_paths 正确打包发送给知识库文档添加端点', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    storageValues.set('user_token', 'test-token')
    const requests = []
    globalThis.fetch = async (url, options = {}) => {
      requests.push({ url, options })
      return new Response(JSON.stringify({ status: 'success', message: '已添加' }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const userStore = useUserStore()
    userStore.userRole = 'admin'
    userStore.token = 'test-token'

    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    const items = ['minio://bucket/kb_test/upload/doc1.txt', 'minio://bucket/kb_test/upload/doc2.txt']
    const params = {
      content_type: 'file',
      content_hashes: {
        'minio://bucket/kb_test/upload/doc1.txt': 'hash1',
        'minio://bucket/kb_test/upload/doc2.txt': 'hash2'
      },
      source_paths: {
        'minio://bucket/kb_test/upload/doc1.txt': 'my_folder/sub/doc1.txt',
        'minio://bucket/kb_test/upload/doc2.txt': 'my_folder/doc2.txt'
      }
    }

    const response = await documentApi.addDocuments('kb_123', items, params)

    assert.equal(response.status, 'success')
    assert.equal(requests.length, 1)
    assert.equal(requests[0].url, '/api/knowledge/databases/kb_123/documents')
    assert.equal(requests[0].options.method, 'POST')
    const parsedBody = JSON.parse(requests[0].options.body)
    assert.deepEqual(parsedBody.items, items)
    assert.deepEqual(parsedBody.params.source_paths, {
      'minio://bucket/kb_test/upload/doc1.txt': 'my_folder/sub/doc1.txt',
      'minio://bucket/kb_test/upload/doc2.txt': 'my_folder/doc2.txt'
    })
  } finally {
    await server.close()
  }
})
