<script setup>
import { computed } from "vue";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { Bot, CheckCheck, Terminal, ChevronRight } from "@lucide/vue";

const props = defineProps({
  item: { type: Object, required: true },
  output: { type: Object, default: null },
});
const emit = defineEmits(["download"]);
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true });
markdown.renderer.rules.image = (tokens, index) =>
  markdown.utils.escapeHtml(`[图片：${tokens[index].content || "附件"}]`);
const content = computed(() => {
  const parts = [];
  for (const part of props.item.content || []) {
    if (part?.type === "input_image") {
      if (typeof part.image_url === "string" && part.image_url.startsWith("data:image/")) {
        parts.push({ type: "image", src: part.image_url });
      }
    } else if (part?.text) {
      const last = parts.at(-1);
      if (last?.type === "text") last.text += part.text;
      else parts.push({ type: "text", text: part.text });
    }
  }
  // 相邻文字合并解析以保留 Markdown，同时保持图片所在的位置。
  return parts.map((part) => part.type === "text"
    ? { type: "text", html: DOMPurify.sanitize(markdown.render(part.text), { ADD_ATTR: ["target", "rel"] }) }
    : part);
});
const toolSettled = computed(
  () => Boolean(props.output) && props.output.status !== "in_progress",
);
const reasoning = computed(() =>
  Object.values(props.item.yuxi?.reasoning || {}).join(""),
);
const user = computed(() => props.item.role === "user");
const tool = computed(() => props.item.type === "function_call");

/** 格式化公开工具数据，原始字符串作为文本显示。 */
function pretty(value) {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

/** 拦截虚拟产物链接，以当前 Thread 凭据下载。 */
function followLink(event) {
  const link = event.target.closest("a");
  if (!link) return;
  const href = link.getAttribute("href");
  if (href?.startsWith("/home/user/workspace/") || href?.startsWith("/home/user/skills/")) {
    event.preventDefault();
    try {
      emit("download", decodeURIComponent(href));
    } catch {
      emit("download", href);
    }
  } else {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
}
</script>

<template>
  <div v-if="tool" class="tool-message">
    <details>
      <summary>
        <Terminal :size="14" /><span>{{ item.name }}</span
        ><span class="tool-status">{{
          toolSettled ? (output.status === "failed" ? "失败" : "已返回") : "调用中"
        }}</span
        ><ChevronRight :size="13" />
      </summary>
      <div class="tool-detail">
        <span class="eyebrow">参数</span>
        <pre>{{ pretty(item.arguments) }}</pre>
        <template v-if="toolSettled"
          ><span class="eyebrow">{{ output.error ? "错误" : "结果" }}</span>
          <pre>{{ pretty(output.error ?? output.output) }}</pre>
        </template>
      </div>
    </details>
  </div>
  <article
    v-else-if="item.type === 'message'"
    class="message"
    :class="{ 'from-user': user }"
    :data-item-id="item.id"
  >
    <div v-if="!user" class="avatar"><Bot :size="17" /></div>
    <div class="message-body">
      <div v-if="!user" class="message-author">
        Agent <span v-if="item.phase === 'commentary'">过程</span>
      </div>
      <details v-if="reasoning" class="reasoning">
        <summary>思考过程</summary>
        <p>{{ reasoning }}</p>
      </details>
      <!-- 输出在 Markdown parser 与 sanitizer 边界处理，禁用原始 HTML。 -->
      <div
        class="bubble markdown"
        :class="{ 'is-streaming': item.status === 'in_progress' && !content.length }"
        @click="followLink"
      >
        <template v-for="(part, index) in content" :key="index">
          <img v-if="part.type === 'image'" class="message-image" :src="part.src" alt="消息图片" />
          <div v-else v-html="part.html"></div>
        </template>
        <p v-if="!content.length && item.status === 'in_progress'" class="typing">•••</p>
      </div>
      <div class="message-meta">
        <span v-if="item.yuxi?.created_at">{{
          new Date(item.yuxi.created_at).toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
          })
        }}</span>
        <span v-if="item.yuxi?.delivery_status === 'failed'">发送失败</span>
        <span v-else-if="item.yuxi?.delivery_status === 'cancelled'"
          >已取消</span
        >
        <CheckCheck v-else-if="user" :size="13" />
        <span v-if="!user && item.status === 'incomplete'">已中断</span>
      </div>
    </div>
  </article>
</template>
