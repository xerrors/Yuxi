<template>
  <div
    class="conversation-item"
    :class="{
      active: currentChatId === chat.id,
      nested,
      'has-status': chat.thread_status === 'loading' || chat.thread_status === 'ready'
    }"
  >
    <button
      type="button"
      class="conversation-select"
      :aria-current="currentChatId === chat.id ? 'page' : undefined"
      @click="$emit('select-chat', chat.id)"
      @dblclick.stop="renameChat"
      @click.middle="$emit('delete-chat', chat.id)"
    >
      <span class="conversation-title">{{ chat.title || '新的对话' }}</span>
      <span class="actions-mask"></span>
      <span
        v-if="chat.thread_status === 'loading' || chat.thread_status === 'ready'"
        class="status-mask"
      ></span>
      <span
        v-if="chat.thread_status === 'loading'"
        class="thread-status thread-status-loading"
        role="status"
        title="正在运行"
      >
        <Loader2 :size="12" />
      </span>
      <span
        v-else-if="chat.thread_status === 'ready'"
        class="thread-status thread-status-ready"
        role="status"
        title="有新回复"
      ></span>
    </button>
    <span class="conversation-actions" @click.stop @dblclick.stop>
      <a-dropdown :trigger="['click']">
        <template #overlay>
          <a-menu>
            <a-menu-item
              key="pin"
              :icon="h(chat.is_pinned ? PinOff : Pin, { size: 14 })"
              @click.stop="$emit('toggle-pin', chat.id)"
            >
              {{ chat.is_pinned ? '取消置顶' : '置顶' }}
            </a-menu-item>
            <a-menu-item key="rename" :icon="h(SquarePen, { size: 14 })" @click.stop="renameChat">
              重命名
            </a-menu-item>
            <a-menu-item
              key="delete"
              :icon="h(Trash2, { size: 14 })"
              @click.stop="$emit('delete-chat', chat.id)"
            >
              删除
            </a-menu-item>
          </a-menu>
        </template>
        <span class="action-btn-wrapper">
          <a-button type="text" class="more-btn" aria-label="对话操作">
            <MoreVertical :size="16" />
          </a-button>
          <Pin v-if="chat.is_pinned" :size="14" class="pinned-indicator" />
        </span>
      </a-dropdown>
    </span>
  </div>
</template>

<script setup>
import { h } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Loader2, MoreVertical, Pin, PinOff, SquarePen, Trash2 } from '@lucide/vue'

const props = defineProps({
  chat: { type: Object, required: true },
  currentChatId: { type: String, default: null },
  nested: { type: Boolean, default: false }
})

const emit = defineEmits(['select-chat', 'delete-chat', 'rename-chat', 'toggle-pin'])

const renameChat = () => {
  let newTitle = props.chat.title || ''
  Modal.confirm({
    title: '重命名对话',
    icon: null,
    closable: false,
    maskClosable: true,
    centered: true,
    width: 400,
    class: 'rename-conversation-modal',
    content: h('div', [
      h('p', { class: 'rename-conversation-description' }, '保持简短且易于识别'),
      h('input', {
        value: newTitle,
        class: 'rename-conversation-input',
        onInput: (event) => {
          newTitle = event.target.value
        }
      })
    ]),
    okText: '保存',
    cancelText: '取消',
    onOk: () => {
      if (!newTitle.trim()) {
        message.warning('标题不能为空')
        return Promise.reject()
      }
      emit('rename-chat', { chatId: props.chat.id, title: newTitle })
    }
  })
}
</script>

<style lang="less">
.rename-conversation-modal {
  .ant-modal-content {
    padding: 22px 24px 20px;
    border-radius: 12px;
  }

  .ant-modal-confirm-title {
    color: var(--gray-900);
    font-size: 18px;
    font-weight: 600;
    line-height: 1.4;
  }

  .ant-modal-confirm-body .ant-modal-confirm-content {
    width: 100%;
    max-width: none !important;
    margin-top: 4px;
  }

  .ant-modal-confirm-btns {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 18px;

    .ant-btn {
      min-width: 68px;
      height: 34px;
      margin-inline-start: 0;
      border-radius: 8px;
      font-size: 14px;
    }
  }
}

.rename-conversation-description {
  margin: 0 0 14px;
  color: var(--gray-500);
  font-size: 13px;
}

.rename-conversation-input {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  color: var(--gray-900);
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  outline: none;

  &:focus {
    border-color: var(--main-400);
    box-shadow: 0 0 0 2px var(--main-50);
  }
}
</style>

<style lang="less" scoped>
.conversation-item {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  height: 32px;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;

  &.nested {
    .conversation-select {
      padding-left: 30px;
    }
  }

  &:hover {
    background: var(--gray-50);

    .actions-mask,
    .conversation-actions {
      opacity: 1;
    }

    .actions-mask {
      background: linear-gradient(to right, transparent, var(--gray-50) 28px);
    }

    .more-btn {
      display: inline-flex;
    }

    .pinned-indicator,
    .thread-status,
    .status-mask {
      display: none;
    }
  }

  &.active {
    background-color: color-mix(in srgb, var(--gray-100) 6%, var(--gray-100));
    color: var(--gray-1000);

    .conversation-title {
      font-weight: 600;
    }

    .status-mask {
      background: linear-gradient(
        to right,
        transparent,
        color-mix(in srgb, var(--gray-100) 6%, var(--gray-100)) 28px
      );
    }
  }

  &:has(.pinned-indicator) {
    .actions-mask,
    .conversation-actions {
      opacity: 1;
    }
  }

  &.has-status:not(:hover) {
    .actions-mask,
    .conversation-actions {
      opacity: 0;
    }
  }
}

.conversation-select {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  height: 100%;
  padding: 0 8px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: left;

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }
}

.conversation-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thread-status {
  position: absolute;
  top: 50%;
  right: 16px;
  display: inline-flex;
  align-items: center;
  color: var(--main-color);
  pointer-events: none;
  transform: translateY(-50%) translateX(50%);
}

.thread-status-loading :deep(svg) {
  animation: thread-status-spin 1s linear infinite;
}

.thread-status-ready {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--main-color);
}

.actions-mask {
  position: absolute;
  inset: 0 0 0 auto;
  width: 56px;
  background: linear-gradient(to right, transparent, var(--main-5) 28px);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.status-mask {
  position: absolute;
  inset: 0 0 0 auto;
  width: 40px;
  background: linear-gradient(to right, transparent, var(--main-5) 24px);
  pointer-events: none;
}

.conversation-actions {
  position: absolute;
  top: 50%;
  right: 4px;
  display: flex;
  align-items: center;
  opacity: 0;
  transform: translateY(-50%);
  transition: opacity 0.2s ease;
}

.action-btn-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
}

.more-btn {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--gray-600);
}

.pinned-indicator {
  color: var(--gray-400);
}

@keyframes thread-status-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
