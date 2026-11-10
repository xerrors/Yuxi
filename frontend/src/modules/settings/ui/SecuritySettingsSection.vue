<template>
  <div class="security-settings-section settings-page">
    <div class="settings-header">
      <div class="settings-header-copy">
        <h2 class="settings-section-title">安全设置</h2>
        <p class="settings-section-description">管理知识库网页导入和远程 Skill 安装允许访问的外部来源。</p>
      </div>
    </div>
    <UrlImportSettingsSection />
    <SkillSettingsSection />
  </div>
</template>

<script setup>
import UrlImportSettingsSection from '@/modules/knowledge/ui/UrlImportSettingsSection.vue'
import SkillSettingsSection from '@/modules/agents/ui/SkillSettingsSection.vue'
</script>
