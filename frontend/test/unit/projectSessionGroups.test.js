import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildProjectSessionGroups,
  deriveProjectWorkStatus
} from '../../src/modules/projects/model/projectSessionGroups.js'

test('项目工作状态与未读独立，人工等待优先于执行中', () => {
  const projects = [{ id: 'project', status: 'active', selection_status: 'selectable' }]
  const sessions = [
    { id: 'research', status: 'in_progress', yuxi: { project_id: 'project', unread: true } },
    { id: 'approval', status: 'requires_action', yuxi: { project_id: 'project', unread: false } }
  ]
  const group = buildProjectSessionGroups(projects, sessions).groups[0]
  assert.equal(group.status, 'requires_action')
  assert.equal(group.unread, true)
  assert.equal(deriveProjectWorkStatus(sessions.slice(0, 1)), 'in_progress')
  assert.equal(deriveProjectWorkStatus([{ status: 'completed' }]), 'completed')
  assert.equal(deriveProjectWorkStatus([]), 'idle')
})

test('项目视图按项目顺序分组并把 implicit 对话放在最后', () => {
  const projects = [
    { id: 'project-a', name: 'Yuxi', selection_status: 'selectable', status: 'active' },
    { id: 'project-b', name: '论文', selection_status: 'selectable', status: 'active' },
    { id: 'deleted', name: '已删除', selection_status: 'selectable', status: 'deleted' }
  ]
  const sessions = [
    { id: 'implicit', yuxi: { project_id: 'implicit-project' }, updated_at: '2026-08-30T12:00:00Z' },
    { id: 'project-b-chat', yuxi: { project_id: 'project-b' }, updated_at: '2026-08-30T11:00:00Z' },
    { id: 'project-a-old', yuxi: { project_id: 'project-a' }, updated_at: '2026-08-30T10:00:00Z' },
    {
      id: 'project-a-pinned',
      yuxi: { project_id: 'project-a', is_pinned: true },
      updated_at: '2026-08-29T10:00:00Z'
    },
    { id: 'deleted-chat', yuxi: { project_id: 'deleted' }, updated_at: '2026-08-30T09:00:00Z' }
  ]

  const result = buildProjectSessionGroups(projects, sessions)

  assert.deepEqual(
    result.groups.map((group) => group.project.id),
    ['project-a', 'project-b']
  )
  assert.deepEqual(
    result.groups[0].sessions.map((session) => session.id),
    ['project-a-pinned', 'project-a-old']
  )
  assert.deepEqual(
    result.otherSessions.map((session) => session.id),
    ['implicit', 'deleted-chat']
  )
})

test('无项目时全部对话仍可在最近分组读取', () => {
  const sessions = [{ id: 'thread-1', yuxi: { project_id: 'implicit' }, created_at: 1788019200 }]

  const result = buildProjectSessionGroups([], sessions)

  assert.deepEqual(result.groups, [])
  assert.equal(result.otherSessions[0].id, 'thread-1')
})

test('最近分组保持按创建时间排序且不受更新时间影响', () => {
  const sessions = [
    {
      id: 'older-renamed', yuxi: {},
      created_at: 1787997600,
      updated_at: '2026-09-01T10:00:00Z'
    },
    {
      id: 'newer-created', yuxi: {},
      created_at: 1788084000,
      updated_at: '2026-08-30T10:00:00Z'
    }
  ]

  const result = buildProjectSessionGroups([], sessions)

  assert.deepEqual(
    result.otherSessions.map((session) => session.id),
    ['newer-created', 'older-renamed']
  )
})

test('侧边栏同时展示项目和最近分组，最近只展示其他对话', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/SessionNavSection.vue', import.meta.url),
    'utf8'
  )
  const projectHeadingIndex = source.indexOf('<span>项目</span>')
  const recentHeadingIndex = source.indexOf('<span>最近</span>')
  const recentSectionStart = source.indexOf('<section class="history-group recent-history-group">')
  const recentSection = source.slice(
    recentSectionStart,
    source.indexOf('</section>', recentSectionStart)
  )

  assert.ok(projectHeadingIndex >= 0)
  assert.ok(projectHeadingIndex < recentHeadingIndex)
  assert.match(
    source,
    /<section\s+v-if="projectsLoading \|\| projectsError \|\| projectGroups\.length"\s+class="history-group project-history-group"/
  )
  assert.ok(recentSectionStart >= 0)
  assert.match(recentSection, /v-if="recentLoading"[^>]*>正在加载对话/)
  assert.match(recentSection, /v-else-if="projectsError"[^>]*>项目加载失败，暂时无法分类对话/)
  assert.match(recentSection, /v-for="chat in otherSessions"/)
})

test('项目默认折叠且提供完整名称提示', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/SessionNavSection.vue', import.meta.url),
    'utf8'
  )

  const uiSource = readFileSync(new URL('../../src/modules/session/model/chatUI.js', import.meta.url), 'utf8')
  assert.match(uiSource, /const expandedProjects = ref\(new Set\(\)\)/)
  assert.match(
    source,
    /const isProjectExpanded = \(projectId\) => expandedProjects\.value\.has\(projectId\)/
  )
  assert.match(source, /class="project-name" :title="group\.project\.name"/)
})

test('项目运行状态仅在折叠时展示', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/SessionNavSection.vue', import.meta.url),
    'utf8'
  )
  assert.match(
    source,
    /group\.status === 'in_progress' && !isProjectExpanded\(group\.project\.id\)/
  )
})

test('页面内创建的 Project 会写入共享侧边栏导航 Owner', () => {
  const layoutSource = readFileSync(
    new URL('../../src/app/layouts/AppLayout.vue', import.meta.url),
    'utf8'
  )
  const selectionSource = readFileSync(
    new URL('../../src/modules/projects/ui/ProjectSelectionSection.vue', import.meta.url),
    'utf8'
  )

  assert.match(layoutSource, /useProjectsStore\(\)/)
  assert.match(selectionSource, /useProjectsStore\(\)/)
  assert.match(selectionSource, /projectsStore\.upsertProject\(project\)/)
})

test('对话选择与操作菜单使用并列按钮语义', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/SessionNavItem.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /class="session-select"/)
  assert.match(source, /type="button"/)
  assert.doesNotMatch(source, /role="button"/)
  assert.doesNotMatch(source, /@keydown\.(?:enter|space)/)
})

test('项目提供带项目上下文的新建入口', () => {
  const navigationSource = readFileSync(
    new URL('../../src/modules/session/ui/SessionNavSection.vue', import.meta.url),
    'utf8'
  )
  const layoutSource = readFileSync(
    new URL('../../src/modules/session/ui/SessionNavigation.vue', import.meta.url),
    'utf8'
  )
  const agentViewSource = readFileSync(new URL('../../src/pages/AgentView.vue', import.meta.url), 'utf8')
  const chatSource = readFileSync(
    new URL('../../src/modules/session/ui/SessionWorkspace.vue', import.meta.url),
    'utf8'
  )

  assert.match(navigationSource, /class="project-status project-status-loading"/)
  assert.match(navigationSource, /class="project-status project-status-ready"/)
  assert.match(navigationSource, /@click\.stop="\$emit\('create-project-chat', group\.project\.id\)"/)
  assert.match(layoutSource, /query: \{ project_id: projectId \}/)
  const createProjectChatHandler = layoutSource.slice(
    layoutSource.indexOf('const handleCreateProjectChat'),
    layoutSource.indexOf('const searchWorkspace')
  )
  assert.match(createProjectChatHandler, /const handleCreateProjectChat = async/)
  assert.ok(
    createProjectChatHandler.indexOf('await router.push') <
      createProjectChatHandler.indexOf('setCurrentThreadId(null)')
  )
  assert.match(agentViewSource, /:initial-project-id="draftProjectId"/)
  assert.match(chatSource, /if \(!threadId\) selectedProjectId\.value = initialProjectId \|\| AUTO_PROJECT_ID/)
  assert.match(
    chatSource,
    /ensureActiveThread\.reset\(\)\s*selectedProjectId\.value = props\.initialProjectId \|\| AUTO_PROJECT_ID/
  )
  assert.doesNotMatch(chatSource, /ensureActiveThread\.reset\(\)\s*selectedProjectId\.value = AUTO_PROJECT_ID/)
})
