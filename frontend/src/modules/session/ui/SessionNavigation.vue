<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { message } from 'ant-design-vue'
import { MessageCirclePlus, PanelLeft, Search } from '@lucide/vue'
import { useChatThreadsStore } from '../model/chatThreads'
import { useChatUIStore } from '../model/chatUI'
import { useProjectsStore } from '@/modules/projects'
import { useAgentStore } from '@/modules/agents'
import { useInfoStore } from '@/modules/settings'
import { useUserStore } from '@/modules/identity'
import { searchWorkspaceFiles } from '@/apis/workspace_api'
import { projectApi } from '@/apis/project_api'
import SessionNavSection from './SessionNavSection.vue'
import GlobalSearchModal from './GlobalSearchModal.vue'

const props = defineProps({
  panel: { type: Boolean, default: false },
  active: { type: Boolean, default: true },
  visible: { type: Boolean, default: true }
})
const route = useRoute()
const router = useRouter()
const chatThreadsStore = useChatThreadsStore()
const agentStore = useAgentStore()
const projectsStore = useProjectsStore()
const infoStore = useInfoStore()
const userStore = useUserStore()
const { threads, currentThreadId, hasMoreThreads, isLoadingMoreThreads, threadCreationInFlight } =
  storeToRefs(chatThreadsStore)
const { projects, isLoading: projectsLoading, error: projectsError } = storeToRefs(projectsStore)
const { sessionsPinned, sessionPeekOpen, projectPendingId } = storeToRefs(useChatUIStore())
// 详情缓存也包含个人助理与子会话，不能直接当作普通导航目录。
const visibleThreads = computed(() => threads.value.filter((thread) =>
  !thread.yuxi.parent_session_id && !agentStore.agentDetails[thread.agent?.id]?.is_personal_assistant
))
const sessionPanel = ref(null)
const sessionTrigger = ref(null)
const sessionSearchOpen = ref(false)
const isAgentView = computed(() => ['AgentComp', 'AgentCompWithThreadId'].includes(route.name))
const activeSessionThreadId = computed(() => (isAgentView.value ? currentThreadId.value : null))
const productName = computed(() => infoStore.branding.name || 'Yuxi')
const visible = computed(() =>
  props.panel ? props.active && (sessionsPinned.value || sessionPeekOpen.value) : props.visible
)
let sessionCloseTimer

/** 打开临时面板，悬停不改变焦点与常驻偏好。 */
function openSessionPeek() {
  clearTimeout(sessionCloseTimer)
  if (!props.active) return
  sessionPeekOpen.value = true
}

/** 将点击和键盘入口的焦点移入当前面板。 */
function openSessionPeekWithFocus() {
  openSessionPeek()
  void nextTick(() => {
    if (props.active) sessionPanel.value?.querySelector('button')?.focus()
  })
}

/** 保留正在接管导航操作的菜单与搜索弹窗。 */
function hasOpenSessionOverlay() {
  return [
    ...document.querySelectorAll('.ant-dropdown, .ant-modal-wrap, .global-search-overlay')
  ].some((el) => el.getClientRects().length)
}

/** 指针离开后延迟收起临时面板，保留面板内键盘操作。 */
function scheduleSessionPeekClose() {
  if (!props.panel || !props.active) return
  clearTimeout(sessionCloseTimer)
  sessionCloseTimer = setTimeout(() => {
    if (sessionPanel.value?.matches(':hover, :focus-within') || hasOpenSessionOverlay()) return
    sessionPeekOpen.value = false
  }, 200)
}

/** 关闭临时展示，显式退出时归还入口焦点。 */
function closeSessionPeek(restoreFocus = false) {
  clearTimeout(sessionCloseTimer)
  sessionPeekOpen.value = false
  if (restoreFocus)
    void nextTick(() => {
      if (props.active) sessionTrigger.value?.focus()
    })
}

/** 切换常驻偏好，收起立即保存并关闭面板。 */
function toggleSessionsPinned() {
  sessionsPinned.value = !sessionsPinned.value
  if (sessionsPinned.value) openSessionPeek()
  else closeSessionPeek(true)
}

/** 搜索入口由普通导航和对话面板各自展示。 */
function openSessionSearch() {
  sessionSearchOpen.value = true
}

/** 只有当前对话页的临时面板接管 Escape。 */
function handleKeydown(event) {
  if (
    props.panel &&
    props.active &&
    event.key === 'Escape' &&
    !sessionsPinned.value &&
    sessionPeekOpen.value &&
    !hasOpenSessionOverlay()
  )
    closeSessionPeek(true)
}

onClickOutside(
  sessionPanel,
  () => {
    if (props.panel && props.active) closeSessionPeek()
  },
  { ignore: [sessionTrigger, '.ant-dropdown', '.ant-modal-root', '.global-search-overlay'] }
)
watch(
  () => props.active,
  (active) => {
    if (!active && props.panel) {
      closeSessionPeek()
      sessionSearchOpen.value = false
    }
  }
)
watch(
  () => props.visible,
  (shown) => {
    if (!shown) sessionSearchOpen.value = false
  }
)
onMounted(() => {
  if (props.panel) window.addEventListener('keydown', handleKeydown)
})
onUnmounted(() => {
  clearTimeout(sessionCloseTimer)
  if (props.panel) window.removeEventListener('keydown', handleKeydown)
})
defineExpose({ openSessionSearch, sessionSearchOpen })

/** 项目加载失败保留原来的重试入口。 */
const loadProjects = () => projectsStore.loadProjects()
const handleSelectChat = (threadId) => {
  if (!threadId) return
  if (!chatThreadsStore.setCurrentThreadId(threadId)) return
  router.push({ name: 'AgentCompWithThreadId', params: { thread_id: threadId } })
}

const handleSearchThreadFound = (thread) => {
  chatThreadsStore.upsertThread(thread)
}

const handleSearchSelectThread = (thread) => {
  if (!thread?.id) return
  chatThreadsStore.upsertThread(thread)
  handleSelectChat(thread.id)
}

const handleCreateSessionFromSearch = () => {
  if (!chatThreadsStore.setCurrentThreadId(null)) return
  router.push({ name: 'AgentComp' })
}

const handleCreateProjectChat = async (projectId) => {
  if (!projectId || projectPendingId.value || threadCreationInFlight.value) return
  const initiatingUserId = userStore.userId
  await router.push({ name: 'AgentComp', query: { project_id: projectId } })
  if (userStore.userId === initiatingUserId) chatThreadsStore.setCurrentThreadId(null)
}

const searchWorkspace = (query) => searchWorkspaceFiles(query)

// 侧边栏搜索到工作区文件后跳转到工作区并打开对应文件
const handleSearchSelectFile = (entry) => {
  if (!entry?.path) return
  router.push({ name: 'WorkspaceComp', query: { open: entry.path } })
}

const handleArchiveChat = async (threadId) => {
  if (!threadId) return
  const initiatingUserId = userStore.userId
  try {
    await chatThreadsStore.archiveThread(threadId)
    if (userStore.userId !== initiatingUserId) return
    if (route.params.thread_id === threadId) {
      await router.replace({ name: 'AgentComp' })
    }
  } catch (error) {
    console.warn('归档对话失败:', error)
  }
}

const handleRenameChat = async ({ chatId, title }) => {
  try {
    await chatThreadsStore.updateThread(chatId, title)
  } catch (error) {
    console.warn('重命名对话失败:', error)
  }
}

const handleTogglePinChat = async (threadId) => {
  const thread = threads.value.find((item) => item.id === threadId)
  if (!thread) return
  try {
    await chatThreadsStore.updateThread(threadId, null, !thread.yuxi.is_pinned)
    await chatThreadsStore.loadThreads()
  } catch (error) {
    console.warn('更新置顶状态失败:', error)
  }
}

const handleRenameProject = async ({ projectId, name }) => {
  if (!projectId || projectPendingId.value) return
  projectPendingId.value = projectId
  const initiatingUserId = userStore.userId
  try {
    const updatedProject = await projectApi.renameProject(projectId, name)
    if (userStore.userId !== initiatingUserId) return
    projectsStore.replaceProject(updatedProject)
    message.success('项目已重命名')
  } catch (error) {
    message.error(error?.message || '重命名项目失败')
  } finally {
    if (userStore.userId === initiatingUserId) projectPendingId.value = null
  }
}

const handleDeleteProject = async (projectId) => {
  if (!projectId || projectPendingId.value) return
  projectPendingId.value = projectId
  const initiatingUserId = userStore.userId
  try {
    const removedThreadIds = await chatThreadsStore.withThreadListUpdate(async () => {
      if (userStore.userId !== initiatingUserId) return []
      await projectApi.deleteProject(projectId)
      if (userStore.userId !== initiatingUserId) return []
      return chatThreadsStore.removeThreadsByProject(projectId)
    })
    if (userStore.userId !== initiatingUserId) return
    projectsStore.removeProject(projectId)
    if (removedThreadIds.includes(route.params.thread_id)) {
      await router.replace({ name: 'AgentComp' })
    }
    message.success('项目已删除，其中对话已归档，项目文件夹已保留')
  } catch (error) {
    message.error(error?.message || '删除项目失败')
  } finally {
    if (userStore.userId === initiatingUserId) projectPendingId.value = null
  }
}
</script>
<template>
  <div class="session-navigation" :class="{ 'session-panel-shell': panel }">
    <div
      v-if="panel && active && !sessionsPinned"
      ref="sessionTrigger"
      class="session-peek-region"
      role="button"
      tabindex="0"
      aria-label="显示对话列表"
      aria-controls="sidebar-session-panel"
      :aria-expanded="sessionPeekOpen"
      @mousemove="openSessionPeek"
      @mouseleave="scheduleSessionPeekClose"
      @focusout="scheduleSessionPeekClose"
      @click="openSessionPeekWithFocus"
      @keydown.enter.prevent="openSessionPeekWithFocus"
      @keydown.space.prevent="openSessionPeekWithFocus"
      @keydown.down.prevent="openSessionPeekWithFocus"
    />
    <Transition name="session-panel">
      <div
        :id="panel ? 'sidebar-session-panel' : 'sidebar-session-navigation'"
        ref="sessionPanel"
        v-show="visible"
        class="session-navigation-content"
        :class="{ 'session-panel-floating': panel && !sessionsPinned }"
        @mouseenter="panel && visible && openSessionPeek()"
        @mouseleave="scheduleSessionPeekClose"
        @focusout="scheduleSessionPeekClose"
      >
        <div v-if="panel" class="session-panel-header">
          <router-link to="/" class="brand-link">
            <span class="brand-name" :title="productName">{{ productName }}</span>
          </router-link>
          <div class="session-panel-actions">
            <button
              type="button"
              class="lucide-icon-btn"
              :class="{ active: sessionSearchOpen }"
              aria-label="搜索"
              title="搜索"
              @click="openSessionSearch"
            >
              <Search :size="17" aria-hidden="true" />
            </button>
            <button
              type="button"
              class="lucide-icon-btn"
              :aria-label="sessionsPinned ? '收起对话侧栏' : '展开对话侧栏'"
              :title="sessionsPinned ? '收起对话侧栏' : '展开对话侧栏'"
              :aria-expanded="sessionsPinned"
              @click="toggleSessionsPinned"
            >
              <PanelLeft :size="17" aria-hidden="true" />
            </button>
          </div>
        </div>
        <div v-if="panel" class="session-panel-nav">
          <RouterLink
            :to="{ name: 'AgentComp' }"
            class="session-panel-new-chat"
            :class="{ active: route.name === 'AgentComp' }"
            active-class=""
          >
            <MessageCirclePlus class="icon" :size="18" aria-hidden="true" />
            <span class="nav-text">新建对话</span>
          </RouterLink>
        </div>
        <SessionNavSection
          :visible="visible"
          class="sidebar-sessions"
          :current-chat-id="activeSessionThreadId"
          :chats-list="visibleThreads"
          :projects="projects"
          :projects-loading="projectsLoading && !projectsStore.hasLoaded"
          :projects-error="projectsStore.hasLoaded ? '' : projectsError"
          :project-pending-id="projectPendingId"
          :has-more-chats="hasMoreThreads"
          :is-loading-more="isLoadingMoreThreads"
          @select-chat="handleSelectChat"
          @archive-chat="handleArchiveChat"
          @rename-chat="handleRenameChat"
          @toggle-pin="handleTogglePinChat"
          @rename-project="handleRenameProject"
          @delete-project="handleDeleteProject"
          @create-project-chat="handleCreateProjectChat"
          @retry-projects="loadProjects"
          @load-more-chats="() => chatThreadsStore.loadMoreThreads()"
        />
      </div>
    </Transition>
    <GlobalSearchModal
      v-model:open="sessionSearchOpen"
      :modes="['session', 'file']"
      default-mode="session"
      :recent-threads="visibleThreads"
      :file-search="searchWorkspace"
      file-placeholder="搜索个人空间文件..."
      file-scope-hint="仅搜索个人空间，不包含知识库文件。"
      @select-thread="handleSearchSelectThread"
      @create-thread="handleCreateSessionFromSearch"
      @thread-found="handleSearchThreadFound"
      @select-file="handleSearchSelectFile"
    />
  </div>
</template>

<style lang="less" scoped>
.session-navigation {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.session-navigation-content {
  --session-navigation-bg: var(--bg-sider);
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.sidebar-sessions {
  flex: 1;
  min-height: 0;
}
:deep(.session-list) {
  padding: 0 8px;
}
.session-panel-shell {
  position: absolute;
  inset: 0 auto 0 0;
  width: 250px;
  z-index: 13;
  pointer-events: none;
}
.session-panel-shell .session-navigation-content {
  --session-navigation-bg: var(--gray-0);
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 250px;
  pointer-events: auto;
  background: var(--session-navigation-bg);
  // 左缘紧贴图标栏的 border-right，不再自绘 border-left，避免出现双层边框
  border-right: 1px solid var(--gray-100);
  transition:
    opacity 0.16s ease,
    transform 0.16s ease,
    top 0.2s ease,
    bottom 0.2s ease,
    left 0.2s ease,
    border-radius 0.2s ease;
  &.session-panel-enter-from,
  &.session-panel-leave-to {
    opacity: 0;
    transform: translateX(-6px);
    pointer-events: none;
  }
  &.session-panel-floating {
    top: 4px;
    bottom: 4px;
    left: 4px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
  }
  .sidebar-sessions {
    margin-top: 0;
    padding-bottom: 12px;
  }
}
.session-peek-region {
  position: absolute;
  top: 0;
  left: 0;
  width: 64px;
  height: 48px;
  pointer-events: auto;
  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: -4px;
    border-radius: 6px;
    &::after {
      content: '对话';
      display: grid;
      height: 100%;
      place-items: center;
      color: var(--gray-600);
      font-size: 12px;
    }
  }
}
.session-panel-header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  margin-top: 6px;
  padding: 0 10px;
  gap: 8px;
}
.brand-link {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  height: 32px;
  padding: 0 4px;
  text-decoration: none;
}
.brand-name {
  min-width: 0;
  overflow: hidden;
  color: var(--gray-1000);
  font-size: 15px;
  font-weight: 650;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-panel-actions {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
  button {
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--gray-600);
    cursor: pointer;
    &:hover,
    &.active,
    &[aria-expanded='true'] {
      background: var(--gray-100);
      color: var(--gray-900);
    }
    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: -2px;
    }
  }
}
.session-panel-nav {
  flex-shrink: 0;
  padding: 12px 8px 8px;
}
.session-panel-new-chat {
  display: flex;
  align-items: center;
  width: 100%;
  height: 32px;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: var(--gray-700);
  text-decoration: none;
  .icon {
    flex: 0 0 16px;
    width: 16px;
    height: 16px;
  }
  .nav-text {
    margin-left: 8px;
    font-size: 14px;
    font-weight: 450;
    line-height: 20px;
  }
  &:hover {
    background: var(--gray-50);
    color: var(--gray-900);
  }
  &.active {
    background: var(--gray-100);
    color: var(--gray-1000);
  }
  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: -2px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .session-panel-shell .session-navigation-content {
    transition: none;
  }
}
</style>
