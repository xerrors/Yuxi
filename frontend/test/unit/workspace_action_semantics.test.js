import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('工作区上传菜单暴露展开状态和菜单语义', () => {
  const source = readSource('../../src/modules/workspace/ui/WorkspaceSidebar.vue')
  const trigger = source.slice(source.indexOf('<button'), source.indexOf('</button>'))
  const menu = source.slice(source.indexOf('<Transition name="file-action-menu">'), source.indexOf('</Transition>'))

  assert.match(trigger, /aria-haspopup="menu"/)
  assert.match(trigger, /:aria-expanded="uploadActionMenuOpen"/)
  assert.match(menu, /class="file-action-menu" role="menu"/)
  assert.equal((menu.match(/role="menuitem"/g) || []).length, 3)
  assert.match(menu, /上传文件夹/)
  assert.match(menu, /onUploadFolderAction/)
})

test('项目创建目录选择器包含尚未绑定的项目目录', () => {
  const source = readSource('../../src/modules/projects/ui/ProjectSelectionSection.vue')
  const pickerStart = source.indexOf('<WorkspaceDirectoryDialog')
  const picker = source.slice(pickerStart, source.indexOf('/>', pickerStart) + 2)

  assert.match(picker, /include-unbound-project-dirs/)
})

test('交付物保存使用工作区路径选择器并传递目标目录', () => {
  const component = readSource('../../src/modules/session/ui/AgentArtifactsCard.vue')
  const api = readSource('../../src/apis/agent_api.js')

  assert.match(component, /<WorkspacePathPicker/)
  assert.match(component, /selectedDestination = ref\('\/saved_artifacts'\)/)
  assert.match(component, /v-model="selectedDestination"/)
  assert.match(api, /destination_path: destinationPath/)
})

test('文件列表工具栏暴露上传与新建文件夹按钮，拖拽仅在可上传时生效', () => {
  const source = readSource('../../src/modules/workspace/ui/WorkspaceFileList.vue')

  assert.match(source, /aria-label="上传文件"/)
  assert.match(source, /aria-label="新建文件夹"/)
  assert.match(source, /\$emit\('upload-file'\)/)
  assert.match(source, /\$emit\('create-directory'\)/)
  assert.match(source, /v-if="uploadable"/)
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
  assert.match(
    source,
    /if \(activeSourceKey\.value !== 'personal' \|\| isReadonlyWorkspacePath\.value \|\| uploadingFile\.value\)\n {4}return/
  )
  assert.match(source, /traverseDirectoryEntry\(directory\.entry\)/)
})
