<template>
  <aside class="workspace-sidebar" aria-label="空间导航">
    <strong class="sidebar-heading">个人空间</strong>
    <nav aria-label="资源位置">
      <button
        :aria-current="activeKey === 'overview' ? 'page' : null"
        @click="$emit('select-overview')"
      >
        <LayoutGrid :size="16" />概览
      </button>
      <button :aria-current="atPersonalRoot ? 'page' : null" @click="$emit('select-personal')">
        <FolderClosed :size="16" />全部文件
      </button>
      <section>
        <h2>资源</h2>
        <button
          v-for="shortcut in shortcuts"
          :key="shortcut.path"
          :aria-current="atShortcut(shortcut.path) ? 'page' : null"
          @click="$emit('select-path', shortcut.path)"
        >
          <component :is="shortcut.icon" :size="16" />{{ shortcut.name }}
        </button>
      </section>
      <section v-for="group in groups" :key="group.name">
        <h2>{{ group.name }}</h2>
        <button
          v-for="kb in group.items"
          :key="kb.kb_id"
          class="knowledge-item"
          :aria-current="isCurrentKnowledgeBase(kb) ? 'page' : null"
          :title="kb.name"
          @click="$emit('select-knowledge-base', kb)"
        >
          <component :is="group.icon" :size="16" aria-hidden="true" /><span>{{ kb.name }}</span>
        </button>
      </section>
      <small v-if="knowledgeListStatus === 'loading' && !knowledgeBases.length" role="status">正在加载知识库…</small>
      <small v-else-if="knowledgeListStatus === 'error'" role="alert">
        知识库加载失败
        <a-button type="link" size="small" @click="$emit('reload-knowledge-bases')">重试</a-button>
      </small>
      <small v-else-if="knowledgeListStatus === 'ready' && !knowledgeBases.length">暂无可访问知识库</small>
    </nav>
    <WorkspaceUsage class="sidebar-usage" />
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { Bookmark, createLucideIcon, FolderClosed, LayersPlus, LayoutGrid } from '@lucide/vue'
import WorkspaceUsage from './WorkspaceUsage.vue'

const props = defineProps({
  activeKey: { type: String, required: true },
  currentPath: { type: String, required: true },
  knowledgeBases: { type: Array, default: () => [] },
  currentUid: { type: String, default: '' },
  knowledgeListStatus: { type: String, default: 'ready' }
})
defineEmits([
  'select-overview',
  'select-personal',
  'select-path',
  'select-knowledge-base',
  'reload-knowledge-bases'
])

const shortcuts = [
  { name: '智能体文件', path: '/agents', icon: LayersPlus },
  { name: '保存的产物', path: '/saved_artifacts', icon: Bookmark }
]
const atShortcut = (path) =>
  props.activeKey === 'personal' &&
  (props.currentPath === path || props.currentPath.startsWith(path + '/'))
const atPersonalRoot = computed(
  () => props.activeKey === 'personal' && !shortcuts.some((item) => atShortcut(item.path))
)
const isCurrentKnowledgeBase = (kb) => props.activeKey === 'knowledge-base:' + kb.kb_id
// FolderUser 和 FolderUsers 来自 Lucide（ISC，见 @lucide/vue 随包许可证）。
const FolderUser = createLucideIcon('folder-user', [
  ['path', { d: 'M10.254 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2V9.255' }],
  ['path', { d: 'M22 22a4 4 0 0 0-8 0' }],
  ['circle', { cx: 18, cy: 15, r: 3 }]
])
const FolderUsers = createLucideIcon('folder-users', [
  ['path', { d: 'M5.254 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2V9.255' }],
  ['circle', { cx: 13, cy: 15, r: 3 }],
  ['path', { d: 'M17 22a4 4 0 0 0-8 0' }],
  ['path', { d: 'M22 22A4 4 0 0 0 20.24 18.686 1 1 0 0 1 20.1 17.143 3 3 0 0 0 19.5 12.402' }]
])

const groups = computed(() =>
  [
    {
      name: '我的知识库',
      icon: FolderUser,
      items: props.knowledgeBases.filter((kb) => kb.created_by === props.currentUid)
    },
    {
      name: '共享知识库',
      icon: FolderUsers,
      items: props.knowledgeBases.filter((kb) => kb.created_by !== props.currentUid)
    }
  ].filter((group) => group.items.length)
)
</script>

<style scoped lang="less">
.workspace-sidebar {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-10);
  border-right: 1px solid var(--gray-150);
}
.sidebar-heading {
  flex: none;
  display: flex;
  align-items: center;
  min-height: var(--workspace-header-content-height);
  padding: 8px 12px;
  color: var(--gray-1000);
  font-size: 16px;
}
nav {
  flex: 1;
  min-height: 0;
  padding: 0 8px 16px;
  overflow-y: auto;
}
.sidebar-usage {
  flex: none;
  padding: 10px 12px 12px;
  border-top: 1px solid var(--gray-150);
}
nav button:where(:not(.ant-btn)) {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 36px;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-800);
  text-align: left;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
nav button:where(:not(.ant-btn)):hover {
  background: var(--gray-50);
}
button[aria-current] {
  background: var(--gray-100);
  color: var(--gray-1000);
  font-weight: 600;
}
nav svg {
  flex: none;
}
.knowledge-item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
h2,
small {
  margin: 16px 10px 4px;
  font-size: 12px;
  color: var(--gray-600);
}
small {
  display: block;
}
</style>
