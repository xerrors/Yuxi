<template>
  <form
    v-if="node.type === 'Form'"
    class="widget-node widget-form"
    :style="nodeStyle"
    @input="formError = ''"
    @submit.prevent="submitForm"
  >
    <WidgetNode
      v-for="(child, index) in node.children"
      :key="index"
      :node="child"
      :disabled="disabled"
      @action="emit('action', $event)"
      @preview="emit('preview', $event)"
    />
    <p v-if="formError" class="widget-notice widget-submit-error" role="alert">{{ formError }}</p>
  </form>
  <fieldset
    v-else-if="node.type === 'RadioGroup'"
    class="widget-radio-group"
    :disabled="disabled || node.disabled"
  >
    <legend class="widget-field-label">
      {{ node.label }}<span v-if="node.required" aria-hidden="true"> *</span>
    </legend>
    <label v-for="option in node.options" :key="option.value" class="widget-radio-option">
      <input
        v-model="fieldValue"
        type="radio"
        :name="node.name"
        :value="option.value"
        :required="node.required"
      />
      <span>{{ option.label }}</span>
    </label>
  </fieldset>
  <label
    v-else-if="['Input', 'Textarea', 'DatePicker'].includes(node.type)"
    :class="['widget-field', { 'widget-field-multiline': node.type === 'Textarea' }]"
  >
    <span class="widget-field-label"
      >{{ node.label }}<span v-if="node.required" aria-hidden="true"> *</span></span
    >
    <textarea
      v-if="node.type === 'Textarea'"
      v-model="fieldValue"
      :name="node.name"
      :rows="node.rows || 3"
      :placeholder="node.placeholder"
      :required="node.required"
      :disabled="disabled || node.disabled"
      maxlength="4000"
    ></textarea>
    <input
      v-else
      v-model="fieldValue"
      :type="node.type === 'DatePicker' ? 'date' : 'text'"
      :name="node.name"
      :placeholder="node.placeholder"
      :min="node.min"
      :max="node.max"
      :required="node.required"
      :disabled="disabled || node.disabled"
      maxlength="4000"
    />
  </label>
  <button
    v-else-if="node.type === 'Button'"
    :type="node.submit ? 'submit' : 'button'"
    :class="[
      'widget-button',
      `widget-button-${node.style || 'primary'}`,
      `widget-button-${node.variant || 'solid'}`,
      `widget-button-${node.size || 'md'}`,
      { 'widget-button-block': node.block, 'widget-pill': node.pill }
    ]"
    :disabled="disabled || node.disabled"
    @click="clickButton"
  >
    <component v-if="node.iconStart" :is="icons[node.iconStart]" :size="16" aria-hidden="true" />
    {{ node.label }}
    <component v-if="node.iconEnd" :is="icons[node.iconEnd]" :size="16" aria-hidden="true" />
  </button>
  <component
    v-else-if="containers[node.type]"
    :is="containers[node.type]"
    :class="[
      'widget-node',
      `widget-${node.type.toLowerCase()}`,
      `widget-size-${node.size || 'md'}`,
      { 'widget-form-card': isFormCard }
    ]"
    :style="nodeStyle"
    :role="node.type === 'ImageGroup' ? 'group' : undefined"
    :aria-label="node.type === 'ImageGroup' ? '图片组' : undefined"
  >
    <button
      v-if="node.type === 'ListViewItem' && node.onClickAction"
      type="button"
      class="widget-listitem-action"
      :disabled="disabled"
      @click="selectItem"
    >
      <WidgetNode
        v-for="(child, index) in node.children"
        :key="index"
        :node="child"
        :disabled="disabled"
        :previewable="false"
      />
    </button>
    <template v-else>
      <WidgetNode
        v-for="(child, index) in node.children"
        :key="index"
        :node="child"
        :disabled="disabled"
        :thumbnail="node.type === 'ImageGroup' || thumbnail"
        :previewable="previewable"
        @preview="previewImage($event, index)"
        @action="emit('action', $event)"
      />
    </template>
  </component>
  <WidgetChart v-else-if="node.type === 'Chart'" :node="node" :disabled="disabled" />
  <WidgetImage
    v-else-if="node.type === 'Image'"
    :node="node"
    :thumbnail="thumbnail"
    :previewable="previewable"
    @preview="emit('preview', { images: [node], index: 0 })"
  />
  <component
    v-else-if="textTags[node.type]"
    :is="textTags[node.type]"
    :class="[`widget-${node.type.toLowerCase()}`, `widget-color-${node.color || 'default'}`]"
    :style="nodeStyle"
    >{{ node.value }}</component
  >
  <component
    v-else-if="node.type === 'Icon'"
    :is="icons[node.name]"
    :class="['widget-icon', `widget-color-${node.color || 'secondary'}`]"
    :size="iconSizes[node.size || 'md']"
    aria-hidden="true"
  />
  <span
    v-else-if="node.type === 'Badge'"
    :class="[
      'widget-badge',
      `widget-tone-${node.color || 'secondary'}`,
      `widget-variant-${node.variant || 'soft'}`,
      `widget-size-${node.size || 'md'}`,
      { 'widget-pill': node.pill }
    ]"
    >{{ node.label }}</span
  >
  <hr v-else-if="node.type === 'Divider'" class="widget-divider" />
  <div v-else-if="node.type === 'Spacer'" class="widget-spacer" aria-hidden="true"></div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { BookOpen, FileText, Lightbulb, Info, Check, Plus, Search, ChevronRight } from '@lucide/vue'
import { createAsyncPanel } from '@/shared/lib/asyncPanel'
import WidgetImage from '@/shared/ui/WidgetImage.vue'
import { widgetFormMessage } from '@/shared/lib/chatWidgets'
defineOptions({
  components: { WidgetChart: createAsyncPanel(() => import('./WidgetChart.vue')) }
})
const props = defineProps({
  node: { type: Object, required: true },
  disabled: Boolean,
  thumbnail: Boolean,
  previewable: { type: Boolean, default: true }
})
const emit = defineEmits(['action', 'preview'])
const fieldValue = ref(props.node.defaultValue || '')
const formError = ref('')
const containers = {
  Card: 'section',
  Row: 'div',
  Col: 'div',
  ListView: 'ul',
  ImageGroup: 'div',
  ListViewItem: 'li'
}
const textTags = { Title: 'h3', Text: 'p', Caption: 'p' }
const icons = {
  'book-open': BookOpen,
  document: FileText,
  lightbulb: Lightbulb,
  info: Info,
  check: Check,
  plus: Plus,
  search: Search,
  'chevron-right': ChevronRight
}
const iconSizes = { xs: 14, sm: 16, md: 20, lg: 24, xl: 32 }
const isFormCard = computed(() => props.node.type === 'Card' && ownsForm(props.node.children))

/** 表单外观由最近的 Card 拥有，Row/Col 不改变归属。 */
function ownsForm(children) {
  return children.some(child =>
    child.type === 'Form' ||
    (child.type !== 'Card' && child.children && ownsForm(child.children))
  )
}

const nodeStyle = computed(() => {
  const node = props.node
  const style = {}
  if (node.gap !== undefined) style.gap = `${node.gap * 4}px`
  if (node.padding !== undefined) style.padding = `${node.padding * 4}px`
  if (node.align)
    style.alignItems = { start: 'flex-start', end: 'flex-end' }[node.align] || node.align
  if (node.justify)
    style.justifyContent =
      {
        start: 'flex-start',
        end: 'flex-end',
        between: 'space-between',
        around: 'space-around',
        evenly: 'space-evenly'
      }[node.justify] || node.justify
  if (node.flex !== undefined) {
    style.flexGrow = node.flex === 'auto' ? 1 : node.flex
    style.flexShrink = 1
    // 有权重的列保留 Row 的换行基准，0 和 auto 按内容计算基准。
    if (node.flex === 0 || node.flex === 'auto') style.flexBasis = 'auto'
  }
  if (textTags[node.type]) {
    const sizes = {
      Title: { xs: 12, sm: 14, md: 16, lg: 18, xl: 20, '2xl': 24 },
      Text: { xs: 12, sm: 13, md: 14, lg: 16, xl: 18 },
      Caption: { sm: 11, md: 12, lg: 13 }
    }[node.type]
    if (node.size) style.fontSize = `${sizes[node.size]}px`
    if (node.weight)
      style.fontWeight = { normal: 400, medium: 500, semibold: 600, bold: 700 }[node.weight]
    if (node.textAlign) style.textAlign = node.textAlign
  }
  return style
})

/** 图片组保留完整顺序，其他容器透传图片自身的预览。 */
function previewImage(preview, index) {
  emit(
    'preview',
    props.node.type === 'ImageGroup' ? { images: props.node.children, index } : preview
  )
}

/** 将推荐项的续聊内容交给消息草稿。 */
function selectItem() {
  if (!props.disabled) emit('action', props.node.onClickAction.text)
}

/** 原生表单验证通过后，将填写内容交给会话输入链路。 */
function submitForm(event) {
  if (props.disabled || !HTMLFormElement.prototype.reportValidity.call(event.currentTarget)) return
  formError.value = ''
  try {
    const text = widgetFormMessage(props.node, new FormData(event.currentTarget))
    emit('action', { type: 'send_message', text })
  } catch (error) {
    formError.value = error.message
  }
}

/** 提交按钮交由所属表单处理，独立按钮只发送普通消息。 */
function clickButton() {
  if (!props.disabled && !props.node.disabled && !props.node.submit)
    emit('action', { type: 'send_message', text: props.node.onClickAction.payload.text })
}
</script>
