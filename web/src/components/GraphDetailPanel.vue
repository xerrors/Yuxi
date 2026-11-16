<template>
  <transition name="slide-fade-left">
    <div class="detail-panel" v-if="visible" @click="handleLinkClick">
      <div class="panel-header">
        <span class="panel-title">{{ title }}</span>
        <X :size="14" class="close-icon" @click="$emit('close')" />
      </div>
      <div class="panel-body">
        <template v-if="item">
          <template v-if="type === 'node'">
            <div :class="rowClass(item.data?.label)">
              <span class="detail-label">名称</span>
              <span class="detail-value">
                <DetailValue
                  :value="item.data?.label"
                  field-key="__label__"
                  :expanded-keys="expandedKeys"
                />
              </span>
            </div>
            <div class="detail-row">
              <span class="detail-label">ID</span>
              <span class="detail-value detail-id">{{ item.id }}</span>
            </div>
            <template v-if="item.data?.original?.labels">
              <div class="detail-row">
                <span class="detail-label">标签</span>
                <span class="detail-value">
                  <a-tag v-for="tag in item.data.original.labels" :key="tag" size="small">{{
                    tag
                  }}</a-tag>
                </span>
              </div>
            </template>
            <template v-if="item.data?.original?.properties">
              <div
                v-for="(value, key) in item.data.original.properties"
                :key="key"
                :class="rowClass(value)"
              >
                <span class="detail-label">{{ key }}</span>
                <span class="detail-value">
                  <DetailValue :value="value" :field-key="key" :expanded-keys="expandedKeys" />
                </span>
              </div>
            </template>
          </template>
          <template v-else-if="type === 'edge'">
            <div :class="rowClass(item.data?.label)">
              <span class="detail-label">类型</span>
              <span class="detail-value">
                <DetailValue
                  :value="item.data?.label"
                  field-key="__type__"
                  :expanded-keys="expandedKeys"
                />
              </span>
            </div>
            <template v-if="item.data?.original?.properties">
              <div
                v-for="(value, key) in filteredEdgeProperties"
                :key="key"
                :class="rowClass(value)"
              >
                <span class="detail-label">{{ key }}</span>
                <span class="detail-value">
                  <DetailValue :value="value" :field-key="key" :expanded-keys="expandedKeys" />
                </span>
              </div>
            </template>
          </template>
        </template>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { computed, reactive, watch, defineComponent, h } from 'vue'
import { X } from '@lucide/vue'

const STACK_THRESHOLD = 50
const TRUNCATE_LIMIT = 100

const DetailValue = defineComponent({
  props: {
    value: [String, Number, Boolean, Object, Array],
    fieldKey: { type: String, required: true },
    expandedKeys: { type: Set, required: true }
  },
  setup(props) {
    return () => {
      const v = props.value
      if (typeof v !== 'string') return String(v ?? '')
      if (v.length <= TRUNCATE_LIMIT) return v
      if (props.expandedKeys.has(props.fieldKey)) {
        return [
          v,
          h(
            'a',
            {
              class: 'expand-link',
              onClick: (e) => {
                e.preventDefault()
                props.expandedKeys.delete(props.fieldKey)
              }
            },
            ' 收起'
          )
        ]
      }
      return [
        v.slice(0, TRUNCATE_LIMIT) + '...',
        h(
          'a',
          {
            class: 'expand-link',
            onClick: (e) => {
              e.preventDefault()
              props.expandedKeys.add(props.fieldKey)
            }
          },
          '展开'
        )
      ]
    }
  }
})

const props = defineProps({
  visible: Boolean,
  item: Object,
  type: String
})

defineEmits(['close'])

const expandedKeys = reactive(new Set())

watch(
  () => props.item,
  () => {
    expandedKeys.clear()
  }
)

const isOverThreshold = (value) => typeof value === 'string' && value.length > STACK_THRESHOLD

const rowClass = (value) => {
  return isOverThreshold(value) ? 'detail-row detail-row--stack' : 'detail-row'
}

const title = computed(() => {
  return props.type === 'node' ? '节点详情' : '关系详情'
})

const filteredEdgeProperties = computed(() => {
  if (!props.item?.data?.original?.properties) return {}
  const properties = props.item.data.original.properties
  const filtered = {}
  const hiddenFields = ['source_id', 'target_id', '_id', 'truncate']
  Object.keys(properties).forEach((key) => {
    if (!hiddenFields.includes(key)) filtered[key] = properties[key]
  })
  return filtered
})
</script>

<style scoped lang="less">
.detail-panel {
  position: absolute;
  top: 60px;
  left: 10px;
  width: 280px;
  max-height: calc(100% - 60px);
  overflow-y: auto;
  z-index: 100;
  background: var(--color-trans-light);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-radius: 8px;
  border: 1px solid var(--gray-100);
  box-shadow: 0 0 4px 0px var(--shadow-2);
  font-size: 13px;
  user-select: auto;

  .panel-header {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    border-bottom: 1px solid var(--gray-200);

    .panel-title {
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-1000);
    }

    .close-icon {
      margin-left: auto;
      cursor: pointer;
      color: var(--gray-500);
      transition: color 0.2s;

      &:hover {
        color: var(--gray-800);
      }
    }
  }

  .panel-body {
    padding: 10px 14px;
  }
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 6px 0;
  border-bottom: 1px solid var(--gray-50);

  &:last-child {
    border-bottom: none;
  }

  &--stack {
    flex-direction: column;
  }

  .detail-label {
    flex-shrink: 0;
    color: var(--gray-500);
    font-size: 12px;
    margin-right: 8px;
    margin-bottom: 2px;
  }

  .detail-value {
    text-align: right;
    color: var(--gray-800);
    font-size: 13px;
    word-break: break-all;

    &.detail-id {
      font-size: 11px;
      color: var(--gray-500);
    }

    :deep(.expand-link) {
      cursor: pointer;
      color: var(--main-700);
      font-size: 12px;
      white-space: nowrap;
      margin-left: 2px;

      &:hover {
        color: var(--main-500);
      }
    }
  }
}

.detail-row--stack {
  .detail-value {
    text-align: left;
  }
}

.slide-fade-left-enter-active {
  transition: all 0.25s ease-out;
}

.slide-fade-left-leave-active {
  transition: all 0.2s cubic-bezier(1, 0.5, 0.8, 1);
}

.slide-fade-left-enter-from,
.slide-fade-left-leave-to {
  transform: translateX(-20px);
  opacity: 0;
}
</style>
