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
          <LibraryBig :size="14" /><span>{{ kb.name }}</span>
        </button>
      </section>
      <small v-if="knowledgeListStatus === 'loading' && !knowledgeBases.length" role="status">正在加载知识库…</small>
      <small v-else-if="knowledgeListStatus === 'error'" role="alert">
        知识库加载失败
        <a-button type="link" size="small" @click="$emit('reload-knowledge-bases')">重试</a-button>
      </small>
      <small v-else-if="knowledgeListStatus === 'ready' && !knowledgeBases.length">暂无可访问知识库</small>
    </nav>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { FolderClosed, LayersPlus, LayoutGrid, LibraryBig, Star } from '@lucide/vue'

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
  { name: '保存的产物', path: '/saved_artifacts', icon: Star }
]
const atShortcut = (path) =>
  props.activeKey === 'personal' &&
  (props.currentPath === path || props.currentPath.startsWith(path + '/'))
const atPersonalRoot = computed(
  () => props.activeKey === 'personal' && !shortcuts.some((item) => atShortcut(item.path))
)
const isCurrentKnowledgeBase = (kb) => props.activeKey === 'knowledge-base:' + kb.kb_id
const groups = computed(() =>
  [
    {
      name: '我的知识库',
      items: props.knowledgeBases.filter((kb) => kb.created_by === props.currentUid)
    },
    {
      name: '共享知识库',
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
  display: flex;
  align-items: center;
  min-height: 54px;
  padding: 10px 12px;
  color: var(--gray-1000);
  font-size: 16px;
}
nav {
  padding: 0 8px 16px;
  overflow-y: auto;
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
