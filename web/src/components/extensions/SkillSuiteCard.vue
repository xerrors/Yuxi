<template>
  <button type="button" class="skill-suite-card" @click="$emit('open', suite)">
    <div class="suite-card-head">
      <div class="suite-card-icon"><Boxes :size="18" /></div>
      <div class="suite-card-title-group">
        <div class="suite-card-title">{{ suite.name }}</div>
        <div class="suite-card-provider">{{ suite.provider }}</div>
      </div>
    </div>

    <p class="suite-card-description">{{ suite.description }}</p>

    <div class="suite-card-footer">
      <span class="suite-card-status">{{ statusText }}</span>
      <span class="suite-card-action">
        {{ installedCount === suite.skills.length ? '查看套件' : '查看并安装' }}
        <ChevronRight :size="15" />
      </span>
    </div>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { Boxes, ChevronRight } from '@lucide/vue'

const props = defineProps({
  suite: { type: Object, required: true },
  installedSlugs: { type: Array, default: () => [] }
})

defineEmits(['open'])

const installedSet = computed(
  () => new Set(props.installedSlugs.map((slug) => String(slug).toLowerCase()))
)
const installedCount = computed(
  () =>
    props.suite.skills.filter((skill) => installedSet.value.has(skill.slug.toLowerCase())).length
)
const statusText = computed(() => {
  if (installedCount.value === props.suite.skills.length) return '已全部安装'
  if (installedCount.value > 0) return `已安装 ${installedCount.value}/${props.suite.skills.length}`
  return `${props.suite.skills.length} 个可安装`
})
</script>

<style lang="less" scoped>
.skill-suite-card {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: 170px;
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-900);
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease;

  &:hover {
    border-color: var(--gray-300);
    background: var(--gray-10);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
  }
}

.suite-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.suite-card-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 34px;
  height: 34px;
  border-radius: 8px;
  background: var(--gray-100);
  color: var(--gray-700);
}

.suite-card-title-group {
  min-width: 0;
}

.suite-card-title {
  overflow: hidden;
  font-size: 15px;
  font-weight: 650;
  line-height: 21px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.suite-card-provider {
  margin-top: 1px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.suite-card-description {
  display: -webkit-box;
  min-height: 40px;
  margin: 14px 0 12px;
  overflow: hidden;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 20px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.suite-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: auto;
}

.suite-card-status {
  color: var(--gray-500);
  font-size: 12px;
}

.suite-card-action {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;
}
</style>
