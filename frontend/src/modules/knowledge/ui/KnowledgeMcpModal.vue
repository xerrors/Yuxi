<template>
  <a-modal
    :open="open"
    :footer="null"
    :width="620"
    title="MCP 接入"
    class="knowledge-mcp-modal"
    @cancel="emit('update:open', false)"
  >
    <div class="mcp-modal-body">
      <p class="mcp-modal-lead">
        外部客户端可通过内置 MCP 服务读取本知识库。连接使用本实例签发的
        <strong>knowledge</strong> API Key，可见范围由 Key 所属用户的资源权限决定。
      </p>

      <section class="mcp-block">
        <div class="mcp-block-label">服务地址</div>
        <div class="mcp-value-row">
          <code class="mcp-code">{{ mcpUrl }}</code>
          <a-button type="text" size="small" class="lucide-icon-btn" @click="copy(mcpUrl)">
            <Copy :size="13" />
            复制
          </a-button>
        </div>
        <p class="mcp-block-hint">使用 Streamable HTTP 连接；远程访问需由管理员在服务端允许当前域名。</p>
      </section>

      <section class="mcp-block">
        <div class="mcp-block-label">请求头</div>
        <div class="mcp-value-row">
          <code class="mcp-code">Authorization: Bearer &lt;API Key&gt;</code>
          <a-button type="text" size="small" class="lucide-icon-btn" @click="copy(headerExample)">
            <Copy :size="13" />
            复制
          </a-button>
        </div>
      </section>

      <section v-if="knowledgeBaseId" class="mcp-block">
        <div class="mcp-block-label">当前知识库</div>
        <div class="mcp-value-row">
          <code class="mcp-code">{{ knowledgeBaseId }}</code>
          <a-button type="text" size="small" class="lucide-icon-btn" @click="copy(knowledgeBaseId)">
            <Copy :size="13" />
            复制
          </a-button>
        </div>
        <p class="mcp-block-hint">先用 list_kbs 确认可见范围，再以 kb_id 调用 query_kb。</p>
      </section>

      <div class="mcp-modal-actions">
        <a-button @click="emit('update:open', false)">关闭</a-button>
        <RouterLink
          :to="{ name: 'SystemManage', query: { tab: 'integration' } }"
          custom
          v-slot="{ href, navigate }"
        >
          <a-button
            :href="href"
            type="primary"
            class="lucide-icon-btn"
            @click="navigate($event)"
          >
            <KeyRound :size="14" />
            管理 API Key
          </a-button>
        </RouterLink>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { Copy, KeyRound } from '@lucide/vue'
import { message } from 'ant-design-vue'
import { copyTextToClipboard } from '@/shared/lib/clipboard'

defineProps({
  open: { type: Boolean, default: false },
  knowledgeBaseId: { type: String, default: '' }
})
const emit = defineEmits(['update:open'])

// 开发与生产入口均代理 API，外部客户端使用浏览器可访问的同源地址。
const mcpUrl = `${window.location.origin}/api/mcp`
const headerExample = 'Authorization: Bearer <API Key>'

/** 通过共享边界复制，兼容局域网 HTTP 访问。 */
async function copy(text) {
  try {
    await copyTextToClipboard(text)
    message.success('已复制')
  } catch {
    message.error('复制失败')
  }
}
</script>

<style lang="less" scoped>
.mcp-modal-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mcp-modal-lead {
  margin: 0;
  color: var(--gray-700);
  font-size: 13px;
  line-height: 1.7;
}

.mcp-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mcp-block-label {
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
}

.mcp-value-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
}

.mcp-code {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--gray-800);
  font-family: var(--mono-font, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 12px;
}

.mcp-block-hint {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.6;
}

.mcp-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
