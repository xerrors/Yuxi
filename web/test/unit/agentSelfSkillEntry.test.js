import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const readSource = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8')

const entrySource = readSource('../../src/components/AgentSelfSkillEntry.vue')
const skillDetailSource = readSource('../../src/components/extensions/SkillDetailView.vue')

const notCreatedBranch = entrySource.slice(
  entrySource.indexOf('<template v-else>'),
  entrySource.indexOf('</template>', entrySource.indexOf('self-skill-actions'))
)

test('专属技能未创建时提供居中的创建与上传两个按钮，默认尺寸且图标在左', () => {
  // 按钮位于空态模板内，与提示文案同处居中区域。
  assert.match(notCreatedBranch, /<a-button[^>]*type="primary"[^>]*@click="createSelfSkill"/)
  assert.match(notCreatedBranch, /<a-button(?![^>]*type=)[^>]*@click="triggerUpload"/)
  assert.doesNotMatch(notCreatedBranch, /size="small"/, '未创建态的按钮使用默认尺寸')

  // 图标在文字左侧。
  assert.match(notCreatedBranch, /<Plus :size="14" \/>\s*\n\s*创建/)
  assert.match(notCreatedBranch, /<Upload :size="14" \/>\s*\n\s*上传/)

  // 按钮容器位于未创建空态内，与提示文案之间由 margin 分隔（具体间距随视觉调整）。
  assert.match(entrySource, /\.self-skill-actions \{[^}]*margin-top: \d+px/)
  assert.ok(
    entrySource.indexOf('self-skill-actions') > entrySource.indexOf('self-skill-hint'),
    '按钮容器在提示文案之后'
  )
})

test('专属技能已创建时只保留右上角 link 入口，不提供上传', () => {
  const header = entrySource.slice(entrySource.indexOf('self-skill-header'), entrySource.indexOf('self-skill-body'))
  assert.match(header, /v-if="exists"/)
  assert.match(header, /type="link"/)
  assert.match(header, /size="small"/)
  assert.match(header, /打开技能管理/)

  // 上传按钮与文件选择框只出现在未创建分支。
  assert.match(notCreatedBranch, /@click="triggerUpload"/)
  assert.match(notCreatedBranch, /type="file"/)
  assert.doesNotMatch(header, /type="file"/)
  const existsBranch = entrySource.slice(
    entrySource.indexOf('<template v-if="exists">'),
    entrySource.indexOf('</template>', entrySource.indexOf('<template v-if="exists">'))
  )
  assert.doesNotMatch(existsBranch, /triggerUpload/)
})

test('技能管理页对专属技能只隐藏可用范围与启用状态，保留运行依赖', () => {
  // 配置 tab 常驻，不整页隐藏。
  assert.match(skillDetailSource, /key: 'config', label: '配置'/)
  assert.doesNotMatch(skillDetailSource, /tab\) => tab\.key !== 'config'/)

  // 可用范围整节（含启用状态与共享范围）对专属技能隐藏。
  assert.match(skillDetailSource, /<section v-if="!isAgentBoundSkill" class="config-section extension-detail-section">/)
  assert.match(skillDetailSource, /<h3>可用范围<\/h3>/)
  assert.match(skillDetailSource, /<h3>运行依赖<\/h3>/)

  // 运行依赖不受绑定状态影响，可编辑判定只看管理权限与内置属性。
  const dependenciesSection = skillDetailSource.slice(
    skillDetailSource.indexOf('<h3>运行依赖</h3>'),
    skillDetailSource.indexOf('运行依赖') + 4000
  )
  assert.doesNotMatch(dependenciesSection.slice(0, 1200), /isAgentBoundSkill/)
  assert.match(skillDetailSource, /canEditSkillDependencies = computed\(\s*\n\s*\(\) => canManageCurrentSkill\.value && !isBuiltinInstalledSkill\.value/)

  // 绑定技能的禁用态提示不再保留。
  assert.doesNotMatch(skillDetailSource, /在这里修改不生效/)
  assert.doesNotMatch(skillDetailSource, /:disabled="!canManageCurrentSkill \|\| isAgentBoundSkill"/)
})

test('专属技能上传走后端派生 slug 的独立入口', () => {
  const agentApi = readSource('../../src/apis/agent_api.js')
  assert.match(agentApi, /uploadAgentSelfSkill: \(agentId, file\)/)
  assert.match(agentApi, /formData\.append\('file', file\)/)
  assert.match(agentApi, /\/self-skill\/upload/)
})
