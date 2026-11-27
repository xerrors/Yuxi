<template>
  <a-button
    class="workspace-search lucide-icon-btn"
    aria-label="搜索个人空间文件"
    title="搜索个人空间文件"
    @click="$emit('search')"
  >
    <Search :size="16" /><span>搜索</span>
  </a-button>
  <a-dropdown-button
    v-if="writable"
    v-model:open="menuOpen"
    type="primary"
    :trigger="['click']"
    :disabled="uploading"
    @click="$emit('upload-file')"
  >
    <span class="upload-label"><Upload :size="15" />{{ uploading ? '上传中…' : '上传' }}</span>
    <template #rightButton>
      <a-button
        type="primary"
        class="upload-menu-button"
        aria-label="更多上传操作"
        aria-haspopup="menu"
        :aria-expanded="menuOpen"
        :disabled="uploading"
      >
        <template #icon><ChevronDown :size="14" /></template>
      </a-button>
    </template>
    <template #overlay>
      <a-menu>
        <a-menu-item key="upload-folder" @click="$emit('upload-folder')">上传文件夹</a-menu-item>
        <a-menu-item key="create-directory" @click="$emit('create-directory')"
          >新建文件夹</a-menu-item
        >
      </a-menu>
    </template>
  </a-dropdown-button>
</template>

<script setup>
import { ref } from 'vue'
import { ChevronDown, Search, Upload } from '@lucide/vue'

const menuOpen = ref(false)
defineProps({ uploading: Boolean, writable: Boolean })
defineEmits(['search', 'upload-file', 'upload-folder', 'create-directory'])
</script>

<style scoped lang="less">
.upload-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.workspace-search {
  gap: 6px;
  font-weight: 500;
  color: var(--gray-800);
}
.upload-menu-button {
  width: var(--workspace-action-size);
  padding: 0;
}
@container (max-width: 560px) {
  .workspace-search {
    width: var(--workspace-action-size);
    padding: 0;
  }
  .workspace-search span {
    display: none;
  }
}
</style>
