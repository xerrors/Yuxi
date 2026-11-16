<template>
  <div class="json-tree-viewer">
    <div v-if="showToolbar" class="json-viewer-toolbar">
      <div class="toolbar-left">
        <span v-if="title" class="viewer-title">{{ title }}</span>
      </div>
      <div class="toolbar-right">
        <button type="button" class="viewer-tool-btn" title="全部展开" @click="triggerExpandAll">
          <UnfoldVertical :size="12" />
          <span>展开</span>
        </button>
        <button type="button" class="viewer-tool-btn" title="全部折叠" @click="triggerCollapseAll">
          <FoldVertical :size="12" />
          <span>折叠</span>
        </button>
        <button type="button" class="viewer-tool-btn" title="复制全部 JSON" @click="copyAllJson">
          <Check v-if="isCopied" :size="12" class="copied-icon" />
          <Copy v-else :size="12" />
          <span>{{ isCopied ? '已复制' : '复制' }}</span>
        </button>
      </div>
    </div>

    <div class="json-tree-content">
      <JsonTreeNode
        :data="data"
        :depth="0"
        :default-expanded-depth="defaultExpandedDepth"
        :is-last="true"
        :expand-all-signal="expandAllSignal"
        :collapse-all-signal="collapseAllSignal"
      />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Check, Copy, FoldVertical, UnfoldVertical } from '@lucide/vue'
import { message } from 'ant-design-vue'
import JsonTreeNode from '@/components/common/JsonTreeNode.vue'
import { copyTextToClipboard } from '@/utils/clipboard'

const props = defineProps({
  data: {
    type: [Object, Array, String, Number, Boolean, null, undefined],
    default: () => ({})
  },
  title: {
    type: String,
    default: ''
  },
  showToolbar: {
    type: Boolean,
    default: true
  },
  defaultExpandedDepth: {
    type: Number,
    default: 1
  }
})

const expandAllSignal = ref(0)
const collapseAllSignal = ref(0)
const isCopied = ref(false)

const triggerExpandAll = () => {
  expandAllSignal.value += 1
}

const triggerCollapseAll = () => {
  collapseAllSignal.value += 1
}

const copyAllJson = async () => {
  try {
    const jsonStr = JSON.stringify(props.data, null, 2)
    await copyTextToClipboard(jsonStr)
    isCopied.value = true
    message.success('已复制完整 JSON')
    setTimeout(() => {
      isCopied.value = false
    }, 1500)
  } catch {
    message.error('复制失败')
  }
}
</script>

<style scoped lang="less">
.json-tree-viewer {
  display: flex;
  flex-direction: column;
  background: transparent;
  overflow: hidden;
}

.json-viewer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 12px;
}

.viewer-title {
  font-weight: 600;
  color: var(--gray-800);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.viewer-tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  height: 24px;
  font-size: 12px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--gray-700);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s ease;

  &:hover {
    background: var(--gray-150);
    color: var(--gray-1000);
  }

  .copied-icon {
    color: var(--color-success-700);
  }
}

.json-tree-content {
  padding: 4px 0;
  overflow-x: auto;
}
</style>
