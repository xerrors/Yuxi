import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('Skill 详情暴露编辑、配置面板及内联 HTML 控件', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  assert.match(source, /<template #panel-editor>/)
  assert.match(source, /<template #panel-config>/)
  assert.match(source, /:show-inline-html-controls="true"/)
})

test('Skill 编辑入口在详情顶部和项目结构中都可访问', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  const actionsStart = source.indexOf('<template #actions>')
  const topBarActions = source.slice(actionsStart, source.indexOf('</template>', actionsStart))
  const treeActionsStart = source.indexOf('<div class="tree-actions">')
  const treeActions = source.slice(
    treeActionsStart,
    source.indexOf('<div class="tree-content">', treeActionsStart)
  )

  assert.match(topBarActions, /aria-label="编辑当前文件"/)
  assert.match(topBarActions, /@click="startEditingCurrentFile"/)
  assert.match(topBarActions, /aria-label="导出 Skill"/)
  assert.match(topBarActions, /aria-label="删除 Skill"/)
  assert.match(treeActions, /aria-label="编辑当前文件"/)
  assert.match(treeActions, /@click="startEditingCurrentFile"/)
})

test('MCP 详情保留信息、工具面板与可访问操作名称', () => {
  const source = readSource('../../src/components/extensions/McpDetailView.vue')

  assert.match(source, /<ExtensionDetailLayout/)
  assert.match(source, /<template #breadcrumb>/)
  assert.match(source, /<template #actions>/)
  assert.match(source, /<template #panel-general>/)
  assert.match(source, /<template #panel-tools>/)
  assert.match(source, /key: 'general', label: '信息', icon: Settings2/)
  assert.match(source, /key: 'tools', label: `工具 \(\$\{tools\.value\.length\}\)`, icon: Wrench/)
  assert.match(source, /:aria-label="testLoading \? '正在测试 MCP' : '测试 MCP'"/)
  assert.match(source, /aria-label="编辑 MCP"/)
  assert.match(source, /:aria-label="`\$\{actionLabel\} MCP`"/)
  assert.match(
    source,
    /:aria-label="`\$\{tool\.name\} \$\{tool\.enabled \? '已启用' : '已禁用'\}`"/
  )
})

test('共享权限开关的可访问名称包含当前状态', () => {
  const source = readSource('../../src/components/ShareConfigForm.vue')

  assert.match(
    source,
    /:aria-label="`\$\{scope\.title\}\$\{scopes\[scope\.key\] \? '已开启' : '已关闭'\}`"/
  )
})

test('保存运行依赖不会重载并覆盖同页尚未保存的范围配置', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  const saveStart = source.indexOf('const saveDependencies = async () =>')
  const saveDependencies = source.slice(saveStart, source.indexOf('onMounted(', saveStart))

  assert.ok(saveStart >= 0)
  assert.doesNotMatch(saveDependencies, /fetchSkillDetail\(\)/)
})

test('文件保存携带修订值并局部更新，切换文件前检查草稿', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  const saveStart = source.indexOf('const saveCurrentFile = async')
  const saveFile = source.slice(saveStart, source.indexOf('const confirmDeleteSkill', saveStart))

  assert.match(saveFile, /expected_revision: fileRevision\.value/)
  assert.doesNotMatch(saveFile, /fetchSkillDetail\(\)/)
  assert.match(source, /confirmDiscardFileDraft\(\)/)
  assert.match(source, /onBeforeRouteLeave/)
})

test('无预览 header 的 HTML 文件在编辑态隐藏模式控件', () => {
  const source = readSource('../../src/components/AgentFilePreview.vue')
  const controlsStart = source.indexOf('showInlineHtmlControls &&')
  const controls = source.slice(
    controlsStart,
    source.indexOf('class="preview-mode-switch', controlsStart)
  )

  assert.ok(controlsStart >= 0)
  assert.match(controls, /!\(canEdit && editMode === 'edit'\)/)
})
