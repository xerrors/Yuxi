import assert from 'node:assert/strict'
import test from 'node:test'

import {
  filterProjects,
  formatRelativeTime,
  projectNameFromPath
} from '../../src/modules/projects/model/projectSelection.js'

test('Project 搜索按名称过滤且无匹配时返回空列表', () => {
  const projects = [{ name: 'Desktop' }, { name: '论文写作' }, { name: 'Agent Skills' }]

  assert.deepEqual(filterProjects(projects, '  agent  '), [{ name: 'Agent Skills' }])
  assert.deepEqual(filterProjects(projects, '不存在'), [])
  assert.equal(filterProjects(projects, ''), projects)
})

test('历史项目时间按分钟到年份显示相对时间', () => {
  const now = Date.parse('2026-08-22T12:00:00Z')

  assert.equal(formatRelativeTime('2026-08-22T11:59:30Z', now), '刚刚')
  assert.equal(formatRelativeTime('2026-08-22T11:55:00Z', now), '5分钟前')
  assert.equal(formatRelativeTime('2026-08-22T09:00:00Z', now), '3小时前')
  assert.equal(formatRelativeTime('2026-08-19T12:00:00Z', now), '3天前')
  assert.equal(formatRelativeTime('2026-06-22T12:00:00Z', now), '2个月前')
  assert.equal(formatRelativeTime('2024-08-22T12:00:00Z', now), '2年前')
  assert.equal(formatRelativeTime('invalid', now), '')
})

test('项目名称从目录路径末段派生', () => {
  assert.equal(projectNameFromPath('/Paper'), 'Paper')
  assert.equal(projectNameFromPath('/a/b/我的项目'), '我的项目')
  assert.equal(projectNameFromPath('/trailing/'), 'trailing')
})

test('保留完整目录名，无有效末段时返回空', () => {
  const longName = 'x'.repeat(180)
  assert.equal(projectNameFromPath(`/${longName}`), longName)
  assert.equal(projectNameFromPath('/'), '')
  assert.equal(projectNameFromPath(''), '')
})
