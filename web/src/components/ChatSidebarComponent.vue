<template>
  <div
    class="chat-sidebar"
    :class="{
      'sidebar-open': chatUIStore.isSidebarOpen,
      'no-transition': isInitialRender,
      'is-floating': isFloating
    }"
  >
    <div class="sidebar-content">
      <div class="sidebar-header">
        <div class="header-brand">
          <img
            v-if="!userStore.isAdmin && (organization.logo || organization.avatar)"
            :src="organization.logo || organization.avatar"
            alt="logo"
            class="brand-logo"
          />
          <div class="header-title">{{ branding.name || organization.name || 'Yuxi' }}</div>
        </div>
        <div class="header-actions">
          <div
            class="toggle-sidebar nav-btn"
            v-if="chatUIStore.isSidebarOpen"
            @click="toggleCollapse"
          >
            <PanelLeftClose size="20" color="var(--gray-800)" />
          </div>
        </div>
      </div>
      <div class="conversation-list-top">
        <button
          type="text"
          @click="createNewChat"
          class="new-chat-btn"
          :disabled="chatUIStore.creatingNewChat"
        >
          <LoaderCircle v-if="chatUIStore.creatingNewChat" size="18" class="loading-icon" />
          <MessageSquarePlus v-else size="18" />
          创建新对话
        </button>
      </div>
      <div class="conversation-list">
        <template v-if="sortedChats.length > 0">
          <div
            v-for="chat in sortedChats"
            :key="chat.id"
            class="conversation-item"
            :class="{ active: currentChatId === chat.id }"
            @click="selectChat(chat)"
            @click.middle="handleMiddleClickDelete(chat.id)"
          >
            <div class="conversation-title">
              <span>{{ chat.title || '新的对话' }}</span>
            </div>
            <div class="actions-mask"></div>
            <div class="conversation-actions" @click.stop>
              <a-dropdown :trigger="['click']">
                <template #overlay>
                  <a-menu>
                    <a-menu-item
                      key="pin"
                      @click.stop="togglePin(chat.id)"
                      :icon="h(chat.is_pinned ? PinOff : Pin, { size: 14 })"
                    >
                      {{ chat.is_pinned ? '取消置顶' : '置顶' }}
                    </a-menu-item>
                    <a-menu-item
                      key="rename"
                      @click.stop="renameChat(chat.id)"
                      :icon="h(Pencil, { size: 14 })"
                    >
                      重命名
                    </a-menu-item>
                    <a-menu-item
                      key="delete"
                      @click.stop="deleteChat(chat.id)"
                      :icon="h(Trash2, { size: 14 })"
                    >
                      删除
                    </a-menu-item>
                  </a-menu>
                </template>
                <div class="action-btn-wrapper">
                  <a-button type="text" class="more-btn">
                    <MoreVertical :size="16" />
                  </a-button>
                  <Pin v-if="chat.is_pinned" :size="14" class="pinned-indicator" />
                </div>
              </a-dropdown>
            </div>
          </div>
        </template>
        <div v-else class="empty-list">暂无对话历史</div>
        <div v-if="hasMoreChats" class="load-more-wrapper">
          <a-button
            type="text"
            class="load-more-btn"
            :loading="isLoadingMore"
            @click="handleLoadMore"
          >
            {{ isLoadingMore ? '加载中...' : '加载更多' }}
          </a-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  PanelLeftClose,
  MessageSquarePlus,
  LoaderCircle,
  Pin,
  PinOff,
  Pencil,
  Trash2,
  MoreVertical
} from 'lucide-vue-next'
import dayjs, { parseToShanghai } from '@/utils/time'
import { useChatUIStore } from '@/stores/chatUI'
import { useInfoStore } from '@/stores/info'
import { useUserStore } from '@/stores/user'
import { storeToRefs } from 'pinia'

// 使用 chatUI store
const chatUIStore = useChatUIStore()
const infoStore = useInfoStore()
const userStore = useUserStore()

const { branding, organization } = storeToRefs(infoStore)

const props = defineProps({
  currentAgentId: {
    type: String,
    default: null
  },
  currentChatId: {
    type: String,
    default: null
  },
  chatsList: {
    type: Array,
    default: () => []
  },
  isInitialRender: {
    type: Boolean,
    default: false
  },
  isFloating: {
    type: Boolean,
    default: false
  },
  singleMode: {
    type: Boolean,
    default: true
  },
  agents: {
    type: Array,
    default: () => []
  },
  selectedAgentId: {
    type: String,
    default: null
  },
  hasMoreChats: {
    type: Boolean,
    default: false
  },
  isLoadingMore: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits([
  'create-chat',
  'select-chat',
  'delete-chat',
  'rename-chat',
  'toggle-pin',
  'toggle-sidebar',
  'open-agent-modal',
  'load-more-chats'
])

// 按置顶和时间倒序排列的对话列表
const sortedChats = computed(() => {
  return [...props.chatsList].sort((a, b) => {
    // 置顶的排在前面
    if (a.is_pinned !== b.is_pinned) {
      return a.is_pinned ? -1 : 1
    }
    const dateA = parseToShanghai(b.created_at)
    const dateB = parseToShanghai(a.created_at)
    if (!dateA || !dateB) return 0
    return dateA.diff(dateB)
  })
})

const createNewChat = () => {
  emit('create-chat')
}

const selectChat = (chat) => {
  emit('select-chat', chat.id)
}

const deleteChat = (chatId) => {
  emit('delete-chat', chatId)
}

const handleMiddleClickDelete = (chatId) => {
  emit('delete-chat', chatId)
}

const renameChat = async (chatId) => {
  try {
    const chat = props.chatsList.find((c) => c.id === chatId)
    if (!chat) return

    let newTitle = chat.title
    Modal.confirm({
      title: '重命名对话',
      content: h('div', { style: { marginTop: '12px' } }, [
        h('input', {
          value: newTitle,
          style: {
            width: '100%',
            padding: '4px 8px',
            border: '1px solid var(--gray-150)',
            background: 'var(--gray-0)',
            borderRadius: '4px'
          },
          onInput: (e) => {
            newTitle = e.target.value
          }
        })
      ]),
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        if (!newTitle.trim()) {
          message.warning('标题不能为空')
          return Promise.reject()
        }
        emit('rename-chat', { chatId, title: newTitle })
      },
      onCancel: () => {}
    })
  } catch (error) {
    console.error('重命名对话失败:', error)
  }
}

const toggleCollapse = () => {
  emit('toggle-sidebar')
}

const handleLoadMore = () => {
  emit('load-more-chats')
}

const togglePin = (chatId) => {
  emit('toggle-pin', chatId)
}
</script>

<style lang="less" scoped>
.chat-sidebar {
  width: 0;
  height: 100%;
  background-color: var(--gray-0);
  transition: all 0.3s ease;
  display: flex;
  flex-direction: column;
  border: none;
  overflow: hidden;

  .sidebar-content {
    // 保持内部宽度，避免折叠时压缩
    width: 280px;
    min-width: 280px;
    height: 100%;
    display: flex;
    flex-direction: column;
    opacity: 1;
    transform: translateX(0);
    transition:
      opacity 0.2s ease,
      transform 0.3s ease;
  }

  &:not(.sidebar-open) .sidebar-content {
    opacity: 0;
    transform: translateX(-12px);
  }

  &.no-transition {
    transition: none !important;
  }

  &.is-floating {
    position: absolute;
    z-index: 100;
    left: 0;
    top: 0;
    bottom: 0;
    height: 100%;

    &.sidebar-open {
      box-shadow: 4px 0 16px rgba(0, 0, 0, 0.08);
      border-right: none;
    }
  }

  &.sidebar-open {
    width: 280px;
    max-width: 300px;
    border-right: 1px solid var(--gray-200);
  }

  .sidebar-header {
    height: var(--header-height);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
    border-bottom: 1px solid var(--gray-50);
    flex-shrink: 0;

    .header-brand {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex: 1;
    }

    .brand-logo {
      width: 26px;
      height: 26px;
      border-radius: 6px;
      object-fit: cover;
      flex-shrink: 0;
      border: 1px solid var(--gray-100);
      background: var(--gray-0);
    }

    .header-title {
      font-weight: 600;
      font-size: 16px;
      color: var(--gray-900);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      // color: var(--gray-600);
    }
  }

  .conversation-list-top {
    padding: 8px 12px;
    // border-bottom: 1px solid var(--gray-200);

    .new-chat-btn {
      width: 100%;
      padding: 8px 12px;
      border-radius: 8px;
      background-color: var(--gray-0);
      color: var(--main-color);
      border: 1px solid var(--gray-150);
      transition: all 0.2s ease;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      box-shadow: 0 3px 4px rgba(0, 10, 20, 0.02);

      &:hover:not(:disabled) {
        box-shadow: 0 3px 4px rgba(0, 10, 20, 0.07);
      }

      &:disabled {
        cursor: not-allowed;
        opacity: 0.7;
      }

      .loading-icon {
        animation: spin 1s linear infinite;
      }
    }
  }

  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }

  .conversation-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;

    .chat-group {
      margin-bottom: 16px;
    }

    .chat-group-title {
      padding: 4px 8px;
      font-size: 12px;
      color: var(--gray-500);
      font-weight: 500;
      text-transform: uppercase;
    }

    .conversation-item {
      display: flex;
      align-items: center;
      padding: 8px 12px;
      border-radius: 6px;
      margin: 4px 0;
      cursor: pointer;
      transition: background-color 0.2s ease;
      position: relative;
      overflow: hidden;

      .conversation-title {
        flex: 1;
        font-size: 14px;
        color: var(--gray-800);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        transition: color 0.2s ease;
        display: flex;
        align-items: center;
        gap: 4px;
      }

      .actions-mask {
        position: absolute;
        right: 0;
        top: 0;
        bottom: 0;
        width: 60px;
        background: linear-gradient(to right, transparent, var(--bg-sider) 20px);
        opacity: 0;
        transition: opacity 0.3s ease;
        pointer-events: none;
      }

      .conversation-actions {
        display: flex;
        align-items: center;
        position: absolute;
        right: 8px;
        top: 50%;
        transform: translateY(-50%);
        opacity: 0;
        transition: opacity 0.3s ease;

        .action-btn-wrapper {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 24px;
          position: relative;
        }

        .pinned-indicator {
          color: var(--main-color);
          opacity: 0.8;
          pointer-events: none;
        }

        .more-btn {
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          color: var(--gray-600);
          background-color: transparent !important;
          display: none;
          justify-content: center;
          align-items: center;
          padding: 0;
          z-index: 1;

          &:hover {
            color: var(--main-500);
          }
        }
      }

      // 默认显示置顶图标
      &:has(.pinned-indicator) {
        .conversation-actions,
        .actions-mask {
          opacity: 1;
        }
        .actions-mask {
          background: linear-gradient(to right, transparent, var(--bg-sider) 30px);
        }
      }

      &:hover {
        background-color: var(--gray-25);

        .actions-mask {
          background: linear-gradient(to right, transparent, var(--gray-25) 20px);
          opacity: 1;
        }

        .conversation-actions {
          opacity: 1;

          .more-btn {
            display: flex;
          }

          .pinned-indicator {
            display: none;
          }
        }
      }

      &.active {
        background-color: var(--gray-50);

        .conversation-title {
          color: var(--main-600);
          font-weight: 500;
        }

        .actions-mask {
          background: linear-gradient(to right, transparent, var(--gray-50) 20px);
          opacity: 1;
        }

        &:has(.pinned-indicator) {
          .actions-mask {
            background: linear-gradient(to right, transparent, var(--gray-50) 30px);
          }
        }
      }
    }

    .empty-list {
      text-align: center;
      margin-top: 20px;
      color: var(--gray-500);
      font-size: 14px;
    }

    .load-more-wrapper {
      text-align: center;
      padding: 12px 8px;

      .load-more-btn {
        color: var(--main-color);
        font-size: 13px;
        &:hover {
          color: var(--main-600);
        }
      }
    }
  }
}

// Scrollbar styling
.conversation-list::-webkit-scrollbar {
  width: 5px;
}
.conversation-list::-webkit-scrollbar-track {
  background: transparent;
}
.conversation-list::-webkit-scrollbar-thumb {
  background: var(--gray-300);
  border-radius: 5px;
}
.conversation-list::-webkit-scrollbar-thumb:hover {
  background: var(--gray-400);
}

.toggle-sidebar.nav-btn {
  cursor: pointer;
  height: 2.5rem;
  display: flex;
  justify-content: center;
  align-items: center;
  border-radius: 8px;
  // padding: 0.5rem;
  transition: background-color 0.3s;

  svg {
    stroke: var(--gray-600);
  }

  &:hover svg {
    stroke: var(--main-color);
  }
}
</style>
