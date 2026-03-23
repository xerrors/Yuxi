<template>
  <div class="file-tree-wrapper" :class="treeClass">
    <a-tree
      :selected-keys="selectedKeys"
      :expanded-keys="expandedKeys"
      :tree-data="treeData"
      :load-data="loadData"
      :show-icon="showIcon"
      :block-node="blockNode"
      :show-line="showLine"
      class="custom-file-tree"
      @update:selected-keys="handleSelectedUpdate"
      @update:expanded-keys="handleExpandedUpdate"
      @select="handleSelect"
    >
      <!-- Custom Icon Slot -->
      <template #icon="{ data, expanded }">
        <slot name="icon" :node="data" :expanded="expanded">
          <template v-if="data.isLeaf">
            <component
              v-if="useFileIcons"
              :is="getFileIcon(data.key)"
              :style="{ color: getFileIconColor(data.key), fontSize: '16px' }"
            />
            <FileText v-else :size="16" class="file-icon" />
          </template>
          <template v-else>
            <FolderOpen v-if="expanded" :size="18" class="folder-icon open" />
            <Folder v-else :size="18" class="folder-icon" />
          </template>
        </slot>
      </template>

      <!-- Custom Title Slot -->
      <template #title="{ data }">
        <div class="tree-node-wrapper" @click="handleNodeClick(data)">
          <div class="tree-node-content">
            <slot name="title" :node="data">
              <span class="node-title-text" :title="data.title">{{ data.title }}</span>
            </slot>
          </div>
          <div class="node-actions" @click.stop v-if="$slots.actions">
            <slot name="actions" :node="data"></slot>
          </div>
        </div>
      </template>
    </a-tree>
  </div>
</template>

<script setup>
import { Folder, FolderOpen, FileText } from 'lucide-vue-next'
import { getFileIcon, getFileIconColor } from '@/utils/file_utils'

const props = defineProps({
  treeData: {
    type: Array,
    required: true,
    default: () => []
  },
  loadData: {
    type: Function,
    default: undefined
  },
  selectedKeys: {
    type: Array,
    default: () => []
  },
  expandedKeys: {
    type: Array,
    default: () => []
  },
  showIcon: {
    type: Boolean,
    default: true
  },
  blockNode: {
    type: Boolean,
    default: true
  },
  showLine: {
    type: Boolean,
    default: false
  },
  treeClass: {
    type: String,
    default: ''
  },
  useFileIcons: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits([
  'update:selectedKeys',
  'update:expandedKeys',
  'select',
  'nodeClick',
  'toggleFolder'
])

const handleSelectedUpdate = (keys) => {
  emit('update:selectedKeys', keys)
}

const handleExpandedUpdate = (keys) => {
  emit('update:expandedKeys', keys)
}

const handleSelect = (selectedKeys, info) => {
  emit('select', selectedKeys, info)
}

const handleNodeClick = (data) => {
  emit('nodeClick', data)

  const isFolder = data.isLeaf === false || (data.children && Array.isArray(data.children))

  if (isFolder) {
    const key = data.key
    const newExpandedKeys = [...props.expandedKeys]
    const index = newExpandedKeys.indexOf(key)

    if (index > -1) {
      newExpandedKeys.splice(index, 1)
    } else {
      newExpandedKeys.push(key)
    }

    emit('update:expandedKeys', newExpandedKeys)
    emit('toggleFolder', data, newExpandedKeys.indexOf(key) > -1)
  }
}
</script>

<style scoped lang="less">
.file-tree-wrapper {
  width: 100%;

  /* 统一节点容器 */
  :deep(.ant-tree-treenode) {
    display: flex;
    align-items: center;
    width: 100%;
    padding: 0 4px;
    height: 32px;

    /* 隐藏切换器 */
    .ant-tree-switcher {
      display: none;
    }

    /* 缩进单元 */
    .ant-tree-indent {
      display: flex;
      align-items: center;
      align-self: stretch;
      &-unit {
        width: 14px;
      }
    }

    /* 内容区域容器 */
    .ant-tree-node-content-wrapper {
      display: flex;
      align-items: center;
      flex: 1;
      min-width: 0;
      height: 32px;
      line-height: 32px;
      padding: 0 4px;
      border-radius: 4px;
      transition: background-color 0.2s;

      /* 图标容器 */
      .ant-tree-iconEle {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 24px;
        height: 100%;
        margin-right: 4px;
        flex-shrink: 0;

        .anticon {
          display: flex;
          align-items: center;
        }
      }

      /* 标题文字容器 */
      .ant-tree-title {
        display: flex;
        align-items: center;
        flex: 1;
        min-width: 0;
        height: 100%;
      }

      &:hover {
        background-color: var(--gray-50);
      }

      &.ant-tree-node-selected {
        background-color: var(--gray-100);
      }
    }
  }
}

.tree-node-wrapper {
  display: flex;
  align-items: center;
  width: 100%;
  height: 100%;
  min-width: 0;
}

.tree-node-content {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}

.node-title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--gray-800);
}

.folder-icon {
  color: #dcb67a;
  fill: #dcb67a;
  fill-opacity: 0.2;
}

.node-actions {
  display: none;
  align-items: center;
  padding-left: 8px;
  flex-shrink: 0;
}

.ant-tree-node-content-wrapper:hover .node-actions {
  display: flex;
}
</style>
