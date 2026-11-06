<template>
  <section class="session-nav-section" :class="{ collapsed }">
    <div v-if="showHistory && !collapsed" class="history-panel">
      <div ref="listElement" class="session-list" @scroll="saveScrollPosition">
        <section
          v-if="projectsLoading || projectsError || projectGroups.length"
          class="history-group project-history-group"
        >
          <button
            type="button"
            class="history-label"
            :aria-expanded="projectsExpanded"
            @click="projectsExpanded = !projectsExpanded"
          >
            <span>项目</span>
            <ChevronDown
              :size="14"
              class="collapse-icon"
              :class="{ collapsed: !projectsExpanded }"
            />
          </button>
          <CollapseTransition>
            <div v-if="projectsExpanded" class="project-list" :aria-busy="projectsLoading">
              <div v-if="projectsLoading" class="sr-only" role="status">正在加载项目...</div>
              <div v-if="projectsLoading" class="nav-skeleton" aria-hidden="true">
                <div v-for="index in 4" :key="index" class="nav-skeleton-row project-skeleton-row">
                  <span class="nav-skeleton-block nav-skeleton-icon"></span>
                  <span class="nav-skeleton-block nav-skeleton-text"></span>
                </div>
              </div>
              <div v-else-if="projectsError" class="list-state list-error" role="alert">
                <span>项目加载失败</span>
                <button type="button" @click="$emit('retry-projects')">重试</button>
              </div>
              <template v-else>
                <section
                  v-for="group in projectGroups"
                  :key="group.project.id"
                  class="project-group"
                >
                  <div
                    class="project-row"
                    :class="{ pending: projectPendingId === group.project.id }"
                  >
                    <button
                      type="button"
                      class="project-toggle"
                      :aria-expanded="isProjectExpanded(group.project.id)"
                      @click="toggleProject(group.project.id)"
                    >
                      <FolderOpen
                        v-if="isProjectExpanded(group.project.id)"
                        :size="17"
                        class="project-icon"
                      />
                      <FolderClosed v-else :size="17" class="project-icon" />
                      <span class="project-name" :title="group.project.name">{{
                        group.project.name
                      }}</span>
                    </button>
                    <span
                      v-if="group.status === 'requires_action' && !isProjectExpanded(group.project.id)"
                      class="project-activity-badge"
                      :class="{ 'needs-action': group.status === 'requires_action' }"
                      role="status"
                    >{{ SESSION_ACTIVITY_LABELS[group.status] }}</span>
                    <span
                      v-else-if="
                        group.status === 'in_progress' && !isProjectExpanded(group.project.id)
                      "
                      class="project-status project-status-loading"
                      role="status"
                      title="项目中有对话正在运行"
                    >
                      <span class="status-spinner" aria-hidden="true"></span>
                    </span>
                    <span
                      v-else-if="group.unread"
                      class="project-status project-status-ready"
                      role="status"
                      title="项目中有新回复"
                    ></span>
                    <button
                      type="button"
                      class="project-action project-create-chat"
                      :aria-label="`在项目“${group.project.name}”中新建对话`"
                      title="在此项目中新建对话"
                      :disabled="projectPendingId === group.project.id"
                      @click.stop="$emit('create-project-chat', group.project.id)"
                    >
                      <Plus :size="16" />
                    </button>
                    <a-dropdown
                      :trigger="['click']"
                      :disabled="projectPendingId === group.project.id"
                    >
                      <template #overlay>
                        <a-menu>
                          <a-menu-item
                            key="rename"
                            :icon="h(SquarePen, { size: 14 })"
                            @click="renameProject(group.project)"
                            >重命名项目</a-menu-item
                          >
                          <a-menu-item
                            key="delete"
                            danger
                            :icon="h(Trash2, { size: 14 })"
                            @click="confirmDeleteProject(group.project)"
                            >删除项目</a-menu-item
                          >
                        </a-menu>
                      </template>
                      <button
                        type="button"
                        class="project-action project-more"
                        aria-label="项目操作"
                        @click.stop
                      >
                        <MoreVertical :size="16" />
                      </button>
                    </a-dropdown>
                  </div>
                  <CollapseTransition>
                    <div v-if="isProjectExpanded(group.project.id)" class="project-sessions">
                      <SessionNavItem
                        v-for="chat in group.visibleSessions"
                        :key="chat.id"
                        :chat="chat"
                        :current-chat-id="currentChatId"
                        nested
                        @select-chat="$emit('select-chat', $event)"
                        @archive-chat="$emit('archive-chat', $event)"
                        @rename-chat="$emit('rename-chat', $event)"
                        @toggle-pin="$emit('toggle-pin', $event)"
                      />
                      <button
                        v-if="group.hasMore"
                        type="button"
                        class="project-show-more"
                        @click="showMoreProjectSessions(group)"
                      >
                        展开显示
                      </button>
                      <div
                        v-if="!group.sessions.length && !chatsLoading && !chatsError"
                        class="project-empty"
                      >
                        暂无对话
                      </div>
                    </div>
                  </CollapseTransition>
                </section>
              </template>
            </div>
          </CollapseTransition>
        </section>

        <section class="history-group recent-history-group">
          <button
            type="button"
            class="history-label"
            :aria-expanded="recentExpanded"
            @click="recentExpanded = !recentExpanded"
          >
            <span>最近</span>
            <ChevronDown :size="14" class="collapse-icon" :class="{ collapsed: !recentExpanded }" />
          </button>
          <CollapseTransition>
            <div v-if="recentExpanded" class="recent-list" :aria-busy="recentLoading">
              <div v-if="recentLoading" class="sr-only" role="status">正在加载对话...</div>
              <div v-if="recentLoading" class="nav-skeleton" aria-hidden="true">
                <div v-for="index in 6" :key="index" class="nav-skeleton-row">
                  <span class="nav-skeleton-block nav-skeleton-text"></span>
                </div>
              </div>
              <div v-else-if="projectsError" class="list-state">项目加载失败，暂时无法分类对话</div>
              <div v-else-if="chatsError" class="list-state list-error" role="alert">
                <span>{{ chatsError }}</span>
                <button type="button" @click="$emit('retry-chats')">重试</button>
              </div>
              <template v-else>
                <SessionNavItem
                  v-for="chat in otherSessions"
                  :key="chat.id"
                  :chat="chat"
                  :current-chat-id="currentChatId"
                  @select-chat="$emit('select-chat', $event)"
                  @archive-chat="$emit('archive-chat', $event)"
                  @rename-chat="$emit('rename-chat', $event)"
                  @toggle-pin="$emit('toggle-pin', $event)"
                />
                <div v-if="!otherSessions.length && !hasMoreChats" class="list-state">
                  暂无对话历史
                </div>
              </template>
            </div>
          </CollapseTransition>
        </section>

        <div
          v-if="!recentExpanded && !recentLoading && !projectsError && chatsError"
          class="list-state list-error"
          role="alert"
        >
          <span>{{ chatsError }}</span>
          <button type="button" @click="$emit('retry-chats')">重试</button>
        </div>

        <button
          v-if="hasMoreChats && !recentLoading && !projectsError && !chatsError"
          type="button"
          class="load-more-btn"
          :disabled="isLoadingMore"
          @click="$emit('load-more-chats')"
        >
          {{ isLoadingMore ? '加载中...' : '加载更多' }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, h, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatUIStore } from '../model/chatUI'
import { message, Modal } from 'ant-design-vue'
import {
  ChevronDown,
  FolderClosed,
  FolderOpen,
  MoreVertical,
  Plus,
  SquarePen,
  Trash2
} from '@lucide/vue'
import SessionNavItem from '@/modules/session/ui/SessionNavItem.vue'
import { SESSION_ACTIVITY_LABELS } from '@/modules/session/model/sessionActivity'
import CollapseTransition from '@/shared/ui/CollapseTransition.vue'
import { buildProjectSessionGroups } from '@/modules/projects'

const props = defineProps({
  visible: { type: Boolean, default: true },
  currentChatId: { type: String, default: null },
  chatsList: { type: Array, default: () => [] },
  chatsLoading: { type: Boolean, default: false },
  chatsError: { type: String, default: '' },
  projects: { type: Array, default: () => [] },
  projectsLoading: { type: Boolean, default: false },
  projectsError: { type: String, default: '' },
  projectPendingId: { type: String, default: null },
  hasMoreChats: { type: Boolean, default: false },
  isLoadingMore: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  showHistory: { type: Boolean, default: true }
})

const emit = defineEmits([
  'select-chat',
  'archive-chat',
  'rename-chat',
  'toggle-pin',
  'load-more-chats',
  'rename-project',
  'delete-project',
  'create-project-chat',
  'retry-projects',
  'retry-chats'
])
const { projectsExpanded, recentExpanded, expandedProjects, projectVisibleCounts, navigationScrollTop } =
  storeToRefs(useChatUIStore())
const listElement = ref(null)
/** 只记录当前展示的列表，避免隐藏实例覆盖滚动位置。 */
function saveScrollPosition() {
  if (props.visible) navigationScrollTop.value = listElement.value?.scrollTop || 0
}
watch(() => props.visible, async (visible) => {
  if (!visible) return
  await nextTick()
  if (props.visible && listElement.value) listElement.value.scrollTop = navigationScrollTop.value
}, { immediate: true })
const INITIAL_PROJECT_SESSIONS = 5
const PROJECT_SESSIONS_STEP = 10
const recentLoading = computed(() => props.projectsLoading || props.chatsLoading)
const groupedNavigation = computed(() =>
  buildProjectSessionGroups(props.projects, props.chatsList)
)
const projectGroups = computed(() =>
  groupedNavigation.value.groups.map((group) => {
    const visibleCount =
      projectVisibleCounts.value[group.project.id] ?? INITIAL_PROJECT_SESSIONS
    return {
      ...group,
      visibleCount,
      visibleSessions: group.sessions.slice(0, visibleCount),
      hasMore: group.sessions.length > visibleCount
    }
  })
)
const otherSessions = computed(() => groupedNavigation.value.otherSessions)

/** 展开当前项目的下一批对话。 */
function showMoreProjectSessions(group) {
  projectVisibleCounts.value[group.project.id] = group.visibleCount + PROJECT_SESSIONS_STEP
}

const isProjectExpanded = (projectId) => expandedProjects.value.has(projectId)
const toggleProject = (projectId) => {
  const next = new Set(expandedProjects.value)
  if (next.has(projectId)) next.delete(projectId)
  else next.add(projectId)
  expandedProjects.value = next
}

const renameProject = (project) => {
  let name = project.name || ''
  Modal.confirm({
    title: '重命名项目',
    icon: null,
    maskClosable: true,
    centered: true,
    width: 400,
    class: 'rename-session-modal',
    content: h('input', {
      value: name,
      class: 'rename-session-input',
      'aria-label': '项目名称',
      onInput: (event) => {
        name = event.target.value
      }
    }),
    okText: '保存',
    cancelText: '取消',
    onOk: () => {
      if (!name.trim()) {
        message.warning('项目名称不能为空')
        return Promise.reject()
      }
      emit('rename-project', { projectId: project.id, name })
    }
  })
}

const confirmDeleteProject = (project) => {
  Modal.confirm({
    title: `删除项目“${project.name}”？`,
    icon: null,
    centered: true,
    okText: '删除项目',
    okButtonProps: { danger: true },
    cancelText: '取消',
    content: '项目中的对话会被归档，项目文件夹和其中的文件会保留。',
    onOk: () => emit('delete-project', project.id)
  })
}
</script>

<style lang="less" scoped>
.session-nav-section {
  display: flex;
  min-height: 0;
  flex-direction: column;
  margin-top: 16px;
  overflow: hidden;
}
.history-panel {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
}
.history-label {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 4px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  &:hover,
  &:focus-visible {
    .collapse-icon {
      opacity: 1;
    }
  }
  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }
}
.collapse-icon {
  opacity: 0;
  transition:
    opacity 0.15s ease,
    transform 0.2s ease;
  &.collapsed {
    transform: rotate(-90deg);
  }
}
.session-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  scrollbar-width: thin;
}
.project-history-group {
  margin-bottom: 16px;
}
.project-group + .project-group {
  margin-top: 2px;
}
.project-row {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 30px;
  border-radius: 8px;
  color: var(--gray-800);
  &:hover,
  &:focus-within {
    background: var(--gray-50);
    .project-action {
      opacity: 1;
      pointer-events: auto;
    }
    .project-status {
      display: none;
    }
  }
  &.pending {
    opacity: 0.55;
    pointer-events: none;
  }
}
.project-toggle {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 4px 0 7px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  &:focus-visible {
    border-radius: 7px;
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }
}
.project-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-icon {
  flex: 0 0 17px;
}
.project-activity-badge {
  flex-shrink: 0;
  padding: 2px 6px;
  border-radius: 10px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  line-height: 16px;

  &.needs-action {
    background: var(--main-100);
    color: var(--main-700);
    font-weight: 600;
  }
}

.project-status {
  position: absolute;
  right: 6px;
  display: inline-flex;
  flex: 0 0 16px;
  align-items: center;
  justify-content: center;
  color: var(--main-color);
}
.project-status-loading .status-spinner {
  width: 14px;
  height: 14px;
  box-sizing: border-box;
  border: 2px solid var(--gray-200);
  border-top-color: var(--gray-600);
  border-radius: 50%;
  animation: project-status-spin 1s linear infinite;
}
.project-status-ready {
  flex-basis: 8px;
  width: 8px;
  height: 8px;
  margin: 0 4px;
  border-radius: 50%;
  background: var(--main-color);
}
.project-action {
  display: inline-flex;
  flex: 0 0 28px;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  &:focus-visible {
    opacity: 1;
    outline: 2px solid var(--main-300);
  }
}
.project-create-chat:hover,
.project-more:hover {
  background: var(--gray-100);
  color: var(--gray-800);
}
.project-empty {
  padding: 3px 8px 7px 30px;
  color: var(--gray-400);
  font-size: 12px;
}
.project-show-more {
  display: block;
  width: 100%;
  padding: 2px 8px 2px 30px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  &:hover {
    color: var(--gray-800);
  }
  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.nav-skeleton-row {
  display: flex;
  height: 32px;
  align-items: center;
  gap: 7px;
  padding: 0 8px;
}
.nav-skeleton-block {
  position: relative;
  display: block;
  flex-shrink: 0;
  overflow: hidden;
  border-radius: 4px;
  background: var(--gray-100);
  &::after {
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, transparent, var(--gray-200), transparent);
    content: '';
    transform: translateX(-100%);
    animation: nav-skeleton-shimmer 2.4s ease-in-out infinite;
  }
}
.nav-skeleton-icon {
  width: 17px;
  height: 17px;
}
.nav-skeleton-text {
  width: 58%;
  max-width: 160px;
  height: 10px;
}
.nav-skeleton-row:nth-child(4n + 2) .nav-skeleton-text {
  width: 45%;
}
.nav-skeleton-row:nth-child(4n + 3) .nav-skeleton-text {
  width: 68%;
}
.nav-skeleton-row:nth-child(4n + 4) .nav-skeleton-text {
  width: 50%;
}
.list-state {
  padding: 18px 8px;
  color: var(--gray-500);
  font-size: 12px;
  text-align: center;
}
.list-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-error-700);
  button {
    border: 0;
    background: transparent;
    color: var(--main-color);
    cursor: pointer;
  }
}
.load-more-btn {
  display: block;
  margin: 8px auto;
  border: 0;
  background: transparent;
  color: var(--main-color);
  cursor: pointer;
  font-size: 12px;
}
@media (hover: none) {
  .project-row {
    min-height: 40px;
    padding-right: 16px;
    &:hover,
    &:focus-within {
      .project-status {
        display: inline-flex;
        right: 2px;
      }
    }
  }
  .project-toggle {
    height: 40px;
  }
  .project-skeleton-row {
    height: 40px;
  }
  .project-action {
    flex-basis: 40px;
    width: 40px;
    height: 40px;
    opacity: 1;
    pointer-events: auto;
  }
}
@media (prefers-reduced-motion: reduce) {
  .project-list,
  .project-sessions,
  .recent-list,
  .collapse-icon {
    transition-duration: 0.01ms !important;
  }
  .nav-skeleton-block::after {
    animation: none;
  }
}
@keyframes nav-skeleton-shimmer {
  0%,
  15% {
    transform: translateX(-100%);
  }
  80%,
  100% {
    transform: translateX(100%);
  }
}
@keyframes project-status-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
