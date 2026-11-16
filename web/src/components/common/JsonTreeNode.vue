<template>
  <div class="json-node-row" :class="{ 'is-root': depth === 0 }">
    <!-- 基础标量值 (String, Number, Boolean, Null, Undefined) -->
    <div v-if="!isContainer" class="json-leaf-line">
      <span v-if="name !== undefined && name !== null" class="json-key" :title="path">
        {{ formattedName }}:&nbsp;
      </span>
      <span :class="['json-val', valTypeClass]">
        {{ formattedScalar }}
      </span>
      <span v-if="!isLast" class="json-comma">,</span>
      <button
        type="button"
        class="json-node-copy-btn"
        title="复制值"
        @click.stop="copyValue(formattedScalarRaw)"
      >
        <Check v-if="isCopied" :size="10" />
        <Copy v-else :size="10" />
      </button>
    </div>

    <!-- 容器类型 (Object 或 Array) -->
    <div v-else class="json-container-block">
      <!-- 容器行头部（可点击折叠/展开） -->
      <div class="json-container-header" @click.stop="toggleExpand">
        <button
          type="button"
          class="json-toggle-btn"
          :aria-expanded="isExpanded"
          :title="isExpanded ? '折叠' : '展开'"
        >
          <ChevronDown v-if="isExpanded" :size="12" />
          <ChevronRight v-else :size="12" />
        </button>

        <span v-if="name !== undefined && name !== null" class="json-key" :title="path">
          {{ formattedName }}:&nbsp;
        </span>

        <span class="json-bracket">{{ isArray ? '[' : '{' }}</span>

        <!-- 折叠时的概要信息 -->
        <span v-if="!isExpanded" class="json-collapsed-preview">
          {{ isArray ? `... ${containerLength} 项` : `... ${containerLength} 个键` }}
        </span>

        <span v-if="!isExpanded" class="json-bracket">{{ isArray ? ']' : '}' }}</span>
        <span v-if="!isExpanded && !isLast" class="json-comma">,</span>

        <button
          type="button"
          class="json-node-copy-btn"
          title="复制该节点 JSON"
          @click.stop="copyNodeJson"
        >
          <Check v-if="isCopied" :size="10" />
          <Copy v-else :size="10" />
        </button>
      </div>

      <!-- 展开时的子节点树 -->
      <div v-if="isExpanded" class="json-container-body">
        <template v-if="isArray">
          <JsonTreeNode
            v-for="(item, index) in data"
            :key="index"
            :data="item"
            :name="index"
            :is-last="index === data.length - 1"
            :depth="depth + 1"
            :default-expanded-depth="defaultExpandedDepth"
            :path="path ? `${path}[${index}]` : `[${index}]`"
            :expand-all-signal="expandAllSignal"
            :collapse-all-signal="collapseAllSignal"
          />
        </template>
        <template v-else>
          <JsonTreeNode
            v-for="(key, index) in objectKeys"
            :key="key"
            :data="data[key]"
            :name="key"
            :is-last="index === objectKeys.length - 1"
            :depth="depth + 1"
            :default-expanded-depth="defaultExpandedDepth"
            :path="path ? `${path}.${key}` : key"
            :expand-all-signal="expandAllSignal"
            :collapse-all-signal="collapseAllSignal"
          />
        </template>
      </div>

      <!-- 展开时的收尾括号 -->
      <div v-if="isExpanded" class="json-container-footer">
        <span class="json-bracket">{{ isArray ? ']' : '}' }}</span>
        <span v-if="!isLast" class="json-comma">,</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Check, ChevronDown, ChevronRight, Copy } from '@lucide/vue'
import { message } from 'ant-design-vue'
import { copyTextToClipboard } from '@/utils/clipboard'
import { formatJsonKey, formatJsonScalar } from '@/utils/jsonTree'

const props = defineProps({
  data: {
    type: [Object, Array, String, Number, Boolean, null, undefined],
    default: undefined
  },
  name: {
    type: [String, Number],
    default: undefined
  },
  isLast: {
    type: Boolean,
    default: true
  },
  depth: {
    type: Number,
    default: 0
  },
  defaultExpandedDepth: {
    type: Number,
    default: 1
  },
  path: {
    type: String,
    default: ''
  },
  expandAllSignal: {
    type: Number,
    default: 0
  },
  collapseAllSignal: {
    type: Number,
    default: 0
  }
})

const isExpanded = ref(props.depth < props.defaultExpandedDepth)
const isCopied = ref(false)

watch(
  () => props.expandAllSignal,
  (val) => {
    if (val > 0) isExpanded.value = true
  }
)

watch(
  () => props.collapseAllSignal,
  (val) => {
    if (val > 0) isExpanded.value = false
  }
)

const isArray = computed(() => Array.isArray(props.data))
const isObject = computed(
  () => props.data !== null && typeof props.data === 'object' && !Array.isArray(props.data)
)
const isContainer = computed(() => isArray.value || isObject.value)

const objectKeys = computed(() => (isObject.value ? Object.keys(props.data || {}) : []))

const containerLength = computed(() => {
  if (isArray.value) return (props.data || []).length
  if (isObject.value) return objectKeys.value.length
  return 0
})

const formattedName = computed(() => formatJsonKey(props.name))

const valTypeClass = computed(() => {
  if (props.data === null) return 'type-null'
  if (props.data === undefined) return 'type-undefined'
  switch (typeof props.data) {
    case 'string':
      return 'type-string'
    case 'number':
      return 'type-number'
    case 'boolean':
      return 'type-boolean'
    default:
      return 'type-other'
  }
})

const formattedScalar = computed(() => formatJsonScalar(props.data))

const formattedScalarRaw = computed(() => {
  if (props.data === null) return 'null'
  if (props.data === undefined) return 'undefined'
  if (typeof props.data === 'string') return props.data
  return String(props.data)
})

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}

const copyValue = async (text) => {
  try {
    await copyTextToClipboard(text)
    isCopied.value = true
    message.success('已复制到剪贴板')
    setTimeout(() => {
      isCopied.value = false
    }, 1500)
  } catch {
    message.error('复制失败')
  }
}

const copyNodeJson = async () => {
  try {
    const jsonStr = JSON.stringify(props.data, null, 2)
    await copyTextToClipboard(jsonStr)
    isCopied.value = true
    message.success('已复制节点 JSON')
    setTimeout(() => {
      isCopied.value = false
    }, 1500)
  } catch {
    message.error('复制失败')
  }
}
</script>

<style scoped lang="less">
.json-node-row {
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--gray-900);
  word-break: break-all;
}

.json-leaf-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  padding: 1px 4px 1px 18px;
  border-radius: 3px;
  transition: background-color 0.1s ease;

  &:hover {
    background-color: var(--gray-100);

    .json-node-copy-btn {
      opacity: 1;
    }
  }
}

.json-container-header {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
  padding: 1px 4px;
  border-radius: 3px;
  transition: background-color 0.1s ease;
  user-select: none;

  &:hover {
    background-color: var(--gray-100);

    .json-node-copy-btn {
      opacity: 1;
    }
  }
}

.json-toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  margin-right: 2px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
}

.json-key {
  color: var(--gray-800);
  font-weight: 500;
}

.json-bracket {
  color: var(--gray-600);
  font-weight: 600;
}

.json-comma {
  color: var(--gray-500);
}

.json-collapsed-preview {
  font-size: 11px;
  color: var(--gray-500);
  background-color: var(--gray-150);
  padding: 0 4px;
  border-radius: 3px;
  margin: 0 4px;
}

.json-container-body {
  padding-left: 14px;
  border-left: 1px dashed var(--gray-200);
  margin-left: 6px;
}

.json-container-footer {
  padding-left: 18px;
}

.json-val {
  &.type-string {
    color: var(--color-success-700);
  }

  &.type-number {
    color: var(--color-info-700);
  }

  &.type-boolean {
    color: var(--color-warning-700);
    font-weight: 600;
  }

  &.type-null,
  &.type-undefined {
    color: var(--gray-400);
    font-style: italic;
  }
}

.json-node-copy-btn {
  opacity: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-left: 6px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s ease;

  &:hover {
    background: var(--gray-200);
    color: var(--gray-1000);
  }
}
</style>
