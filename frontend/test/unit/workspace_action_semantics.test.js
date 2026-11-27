import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('顶部工具栏提供文件上传、文件夹上传和新建目录，侧栏只显示导航', () => {
  const actions = readSource('../../src/modules/workspace/ui/WorkspaceActions.vue')
  const sidebar = readSource('../../src/modules/workspace/ui/WorkspaceSidebar.vue')
  for (const action of ['upload-file', 'upload-folder', 'create-directory']) {
    assert.ok(actions.includes("$emit('" + action + "')"), action)
    assert.ok(!sidebar.includes(action))
  }
})

test('交付物保存使用工作区路径选择器并传递目标目录', () => {
  const component = readSource('../../src/modules/session/ui/AgentArtifactsCard.vue')
  const api = readSource('../../src/apis/agent_api.js')

  assert.match(component, /<WorkspacePathPicker/)
  assert.match(component, /selectedDestination = ref\('\/saved_artifacts'\)/)
  assert.match(component, /v-model="selectedDestination"/)
  assert.match(api, /destination_path: destinationPath/)
})

test('文件列表允许在可写目录拖拽上传', () => {
  const source = readSource('../../src/modules/workspace/ui/WorkspaceFileList.vue')
  assert.match(source, /emit\('upload-drop', roots\)/)
  // 不可上传（知识库/只读目录）时不得阻止默认行为或显示投放层。
  assert.match(source, /if \(!props\.uploadable\) return/)
  assert.match(source, /dragActive = computed\(\(\) => props\.uploadable && dragOver\.value\)/)
  // 拖拽离开壳时必须复位，行重建丢失 leave 也不能滞留覆盖层。
  assert.match(source, /event\.currentTarget\.contains\(related\)/)
})

test('拖拽上传在页面侧仅作用于个人空间非只读目录', () => {
  const source = readSource('../../src/pages/WorkspaceView.vue')

  assert.match(source, /:uploadable="personalUploadEnabled"/)
  assert.match(source, /if \(!personalUploadEnabled\.value \|\| uploadingFile\.value\) return/)
  assert.match(source, /traverseDirectoryEntry\(directory\.entry\)/)
})
