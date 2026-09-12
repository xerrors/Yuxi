import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { isKnowledgeBaseInScope } from '../../src/utils/shareConfig.js'

const source = (path) => readFileSync(new URL('../../src/' + path, import.meta.url), 'utf8')

test('团队与我的按共享配置分类，团队库创建者不会归入个人库', () => {
  const team = {
    created_by: 'current-user',
    share_config: {
      version: 2,
      read_scope: { access_level: 'department', department_ids: [1] },
      manage_scope: null
    }
  }
  const personal = {
    created_by: 'current-user',
    share_config: { version: 2, read_scope: null, manage_scope: null }
  }
  assert.equal(isKnowledgeBaseInScope(team, 'team'), true)
  assert.equal(isKnowledgeBaseInScope(team, 'mine'), false)
  assert.equal(isKnowledgeBaseInScope(personal, 'mine'), true)
  assert.equal(isKnowledgeBaseInScope(personal, 'team'), false)
})

test('业务管理员身份与辅导员身份独立，空业务角色不能继承团队权限', async () => {
  const script = source('stores/user.js')
    .replace(/^import .*$/gm, '')
    .replaceAll('export ', '')
  let profile = { id: 1, role: 'user', business_roles: ['business_admin'] }
  const user = runInNewContext(script + '\nuseUserStore()', {
    defineStore: (_, setup) => setup,
    ref: (value) => ({ value }),
    computed: (getter) => ({
      get value() {
        return getter()
      }
    }),
    localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
    authApi: {
      login: async () => ({ ...profile, access_token: 'test', user_id: profile.id }),
      getCurrentUser: async () => profile
    },
    useAgentStore: () => ({ reset() {} }),
    console
  })
  await user.login({})
  assert.equal(user.canManageTeamKnowledge.value, true)
  assert.equal(user.canManagePersonalKnowledge.value, false)
  profile = { id: 2, role: 'user', business_roles: ['counselor'] }
  await user.getCurrentUser()
  assert.equal(user.canManageTeamKnowledge.value, false)
  assert.equal(user.canManagePersonalKnowledge.value, true)
  profile = { id: 3, role: 'user', business_roles: [] }
  await user.getCurrentUser()
  assert.equal(user.canManageTeamKnowledge.value, false)
  assert.equal(user.canManagePersonalKnowledge.value, false)
})

test('当前分类控制创建入口及个人共享配置', () => {
  const view = source('views/DataBaseView.vue')
  assert.match(view, /label: '团队', value: 'team'/)
  assert.match(view, /label: '我的', value: 'mine'/)
  assert.match(view, /isKnowledgeBaseInScope\(database, knowledgeScope\.value\)/)
  assert.match(view, /v-if="canCreateCurrentScope"/)
  assert.match(view, /:default-personal="knowledgeScope === 'mine'"/)
  const create = source('components/knowledge/DatabaseCreateFlowModal.vue')
  assert.match(create, /props\.defaultPersonal/)
  assert.match(create, /read_scope: \{ access_level: 'department', department_ids: \[userStore\.departmentId\] \}/)
})
