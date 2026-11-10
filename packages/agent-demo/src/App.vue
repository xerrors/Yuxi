<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import {
  Paperclip,
  Image,
  Bot,
  Smartphone,
  Tablet,
  Settings2,
  Plus,
  PanelLeft,
  Send,
  Square,
  ChevronDown,
  MessageSquare,
  ArrowDown,
  Download,
  RefreshCw,
  X,
  ArrowRight,
  WifiOff,
} from "@lucide/vue";
import { questionHasOptions } from "./protocol.js";
import { useDemo } from "./useDemo.js";
import ChatMessage from "./ChatMessage.vue";
import DebugWorkbench from "./DebugWorkbench.vue";

const demo = useDemo();
const {
  config,
  activeApp,
  state,
  items,
  turn,
  running,
  waitpoint,
  waitingForUser,
} = demo;
const workbenchOpen = ref(window.innerWidth >= 1200);
const sidebarOpen = ref(false);
const list = ref(null);
const composer = ref(null);
const attachmentPicker = ref(null);
const imagePicker = ref(null);
const narrow = ref(window.innerWidth < 1200);
const drawer = ref(null);
let drawerTrigger;
const activeAgent = computed(() =>
  state.agents.find((agent) => agent.id === state.agentId),
);
const activeUser = computed(() =>
  activeApp.value.users.find((user) => user.id === activeApp.value.userId),
);
const visibleItems = computed(() =>
  items.value.filter((item) =>
    ["message", "function_call"].includes(item.type),
  ),
);
const outputs = computed(() =>
  items.value.filter((item) => item.type === "function_call_output"),
);
const blocked = computed(
  () =>
    !state.connected ||
    !state.agentId ||
    state.sending ||
    !!state.pending ||
    waitingForUser.value,
);
const statusText = computed(() => {
  if (state.connecting) return "连接中";
  if (!state.connected) return "未连接";
  if (state.stream === "reconnecting") return "正在重连";
  if (state.stream === "error") return "订阅已停止";
  const labels = {
    queued: "等待执行",
    in_progress: "正在回复",
    requires_action: "等待中",
    failed: "执行失败",
    cancelled: "已停止",
  };
  if (turn.value?.status && labels[turn.value.status])
    return labels[turn.value.status];
  return "已连接";
});

/** 只在用户停留于底部时跟随流式消息。 */
function scrollBottom() {
  state.followScroll = true;
  if (list.value) list.value.scrollTop = list.value.scrollHeight;
}
watch(
  items,
  async () => {
    await nextTick();
    if (state.followScroll) scrollBottom();
  },
  { deep: true },
);
watch(
  () => state.threadId,
  async () => {
    await nextTick();
    scrollBottom();
  },
);
watch(
  () => state.draft,
  async () => {
    await nextTick();
    if (!composer.value) return;
    composer.value.style.height = "auto";
    composer.value.style.height = `${Math.min(composer.value.scrollHeight, 120)}px`;
  },
);
const resize = () => {
  narrow.value = window.innerWidth < 1200;
};
window.addEventListener("resize", resize);
onUnmounted(() => window.removeEventListener("resize", resize));

watch(
  [workbenchOpen, narrow],
  async ([open, isNarrow]) => {
    if (open && isNarrow) {
      drawerTrigger = document.activeElement;
      await nextTick();
      if (drawer.value && !drawer.value.open) drawer.value.showModal();
    } else if (drawerTrigger?.isConnected) {
      const trigger = drawerTrigger;
      drawerTrigger = null;
      await nextTick();
      trigger.focus();
    }
  },
  { immediate: true },
);

/** 按 Run 与 call ID 关联公开工具结果。 */
function toolOutput(item) {
  return outputs.value.find(
    (output) =>
      output.yuxi?.call_item_id === item.id ||
      (output.call_id === item.call_id &&
        output.yuxi?.run_id === item.yuxi?.run_id),
  );
}

/** 选择会话并收起手机侧栏。 */
function chooseThread(id) {
  sidebarOpen.value = false;
  void demo.selectThread(id);
}

/** IM 输入框保留中文输入法确认与 Shift 换行。 */
function onKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void demo.send();
  }
}
</script>

<template>
  <div
    class="demo-shell"
    :class="[config.layout, { 'workbench-open': workbenchOpen }]"
  >
    <header class="demo-header">
      <div class="brand">
        <span class="brand-mark"><Bot :size="19" /></span><strong>Yuxi</strong
        ><span class="brand-divider"></span><span>Agent Demo</span>
      </div>
      <div class="header-actions">
        <div class="layout-switch" aria-label="设备布局">
          <button
            :class="{ active: config.layout === 'phone' }"
            :aria-pressed="config.layout === 'phone'"
            @click="
              config.layout = 'phone';
              sidebarOpen = false;
            "
          >
            <Smartphone :size="15" />手机 <span>9:16</span></button
          ><button
            :class="{ active: config.layout === 'tablet' }"
            :aria-pressed="config.layout === 'tablet'"
            @click="
              config.layout = 'tablet';
              sidebarOpen = false;
            "
          >
            <Tablet :size="15" />平板 <span>4:3</span>
          </button>
        </div>
        <button
          class="debug-toggle"
          :class="{ active: workbenchOpen }"
          :aria-expanded="workbenchOpen"
          @click="workbenchOpen = !workbenchOpen"
        >
          <Settings2 :size="16" /><span>调试台</span>
        </button>
      </div>
    </header>
    <main class="demo-stage">
      <div class="device-area">
        <div class="device-caption">
          <span class="status-dot" :class="{ online: state.connected }"></span
          ><span>{{ activeApp.name }}</span
          ><span class="caption-divider">/</span
          ><span>{{ activeUser.name }}</span
          ><span class="device-label">{{
            config.layout === "phone" ? "PHONE" : "TABLET"
          }}</span>
        </div>
        <section class="device-frame" aria-label="Agent 对话">
          <div
            v-if="sidebarOpen"
            class="sidebar-scrim"
            @click="sidebarOpen = false"
          ></div>
          <aside
            class="conversation-sidebar"
            :class="{ open: sidebarOpen }"
            aria-label="对话列表"
          >
            <div class="sidebar-head">
              <strong>对话</strong
              ><button
                class="icon-button sidebar-close"
                aria-label="关闭对话列表"
                @click="sidebarOpen = false"
              >
                <X :size="17" />
              </button>
            </div>
            <button
              class="new-conversation"
              :disabled="!state.connected || !state.agentId"
              @click="
                demo.newThread();
                sidebarOpen = false;
              "
            >
              <Plus :size="17" />新对话
            </button>
            <div class="section-label conversation-label">
              <span>最近对话</span
              ><button
                class="icon-button"
                aria-label="刷新对话列表"
                :disabled="!state.connected || state.loadingThreads"
                @click="demo.loadThreads()"
              >
                <RefreshCw :size="13" :class="{ spin: state.loadingThreads }" />
              </button>
            </div>
            <div class="conversation-list">
              <button
                v-for="thread in state.threads"
                :key="thread.id"
                class="conversation-option"
                :class="{ selected: thread.id === state.threadId }"
                @click="chooseThread(thread.id)"
              >
                <MessageSquare :size="15" /><span
                  ><strong>{{ thread.yuxi.title || "新对话" }}</strong
                  ><small>{{
                    thread.last_active_at
                      ? new Date(thread.last_active_at * 1000).toLocaleDateString(
                          "zh-CN",
                          { month: "short", day: "numeric" },
                        )
                      : ""
                  }}</small></span
                ><span
                  v-if="thread.status === 'in_progress'"
                  class="status-dot online"
                ></span>
              </button>
              <p v-if="!state.threads.length" class="empty-list">
                {{
                  state.loadingThreads ? "正在加载…" : "你的对话将保存在这里"
                }}
              </p>
              <button
                v-if="state.hasMore"
                class="text-button load-more"
                :disabled="state.loadingThreads"
                @click="demo.loadThreads(true)"
              >
                加载更多
              </button>
            </div>
            <div class="sidebar-user">
              <span class="user-avatar">{{ activeUser.name.slice(0, 1) }}</span
              ><span
                >{{ activeUser.name }}<small>{{ activeApp.name }}</small></span
              >
            </div>
          </aside>
          <div class="chat-panel">
            <header class="chat-header">
              <button
                class="icon-button sidebar-toggle"
                aria-label="打开对话列表"
                :aria-expanded="sidebarOpen"
                @click="sidebarOpen = !sidebarOpen"
              >
                <PanelLeft :size="19" />
              </button>
              <div class="agent-heading">
                <div class="agent-selector">
                  <select
                    :value="state.agentId"
                    aria-label="选择 Agent"
                    :disabled="!state.agents.length"
                    @change="demo.selectAgent($event.target.value)"
                  >
                    <option v-if="!state.agents.length" value="">
                      Yuxi Agent
                    </option>
                    <option
                      v-for="agent in state.agents"
                      :key="agent.id"
                      :value="agent.id"
                    >
                      {{ agent.name }}
                    </option></select
                  ><ChevronDown :size="13" />
                </div>
                <span class="agent-status"
                  ><span
                    class="status-dot"
                    :class="{ online: state.connected, working: running }"
                  ></span
                  >{{ statusText }}</span
                >
              </div>
              <div class="chat-toolbar">
                <button
                  class="icon-button new-chat-button"
                  aria-label="新对话"
                  :disabled="!state.connected || !state.agentId"
                  @click="demo.newThread()"
                >
                  <Plus :size="20" /></button
                ><button
                  class="icon-button mobile-debug"
                  aria-label="打开调试台"
                  @click="workbenchOpen = !workbenchOpen"
                >
                  <Settings2 :size="18" />
                </button>
              </div>
            </header>
            <div
              v-if="state.error || state.storageError"
              class="error-notice"
              role="alert"
            >
              <WifiOff :size="15" /><span>{{
                state.error || state.storageError
              }}</span
              ><button
                class="icon-button"
                aria-label="关闭提示"
                @click="
                  state.error = '';
                  state.storageError = '';
                "
              >
                <X :size="14" />
              </button>
            </div>
            <div v-if="state.pending && !state.sending" class="retry-notice">
              <span>接收结果未知，重试会沿用原请求。</span
              ><button class="text-button" @click="demo.retry()">重试</button
              >
            </div>
            <div
              ref="list"
              class="message-list"
              role="log"
              aria-label="对话消息"
              @scroll="
                state.followScroll =
                  list.scrollHeight - list.scrollTop - list.clientHeight < 90
              "
            >
              <button v-if="state.itemPage.hasMore" class="text-button" :disabled="state.itemPage.loading" @click="demo.loadEarlier()">
                加载更早消息
              </button>
              <div v-if="!visibleItems.length" class="chat-welcome">
                <div class="welcome-icon">
                  <Bot :size="29" :stroke-width="1.6" />
                </div>
                <h1>
                  {{
                    state.connected ? "有什么可以帮你？" : "和 Agent 开始对话"
                  }}
                </h1>
                <p>
                  {{
                    state.connected
                      ? activeAgent?.description ||
                        "从一个问题开始，让想法成为结果。"
                      : "在调试台填入 API Key，即可连接你的智能体。"
                  }}
                </p>
                <button
                  v-if="!state.connected"
                  class="welcome-connect"
                  @click="workbenchOpen = true"
                >
                  配置连接<ArrowRight :size="15" />
                </button>
                <div v-else-if="!state.agents.length" class="no-agent">
                  当前 Key 没有可见的 Agent。
                </div>
                <div v-else class="suggestions">
                  <button
                    v-for="prompt in [
                      '介绍一下你的能力',
                      '帮我制定一个学习计划',
                      '把复杂问题拆成可执行步骤',
                    ]"
                    :key="prompt"
                    @click="
                      state.draft = prompt;
                      composer?.focus();
                    "
                  >
                    {{ prompt }}<ArrowRight :size="14" />
                  </button>
                </div>
              </div>
              <div v-else class="message-date">
                {{ state.snapshot?.yuxi.title || "对话" }}
              </div>
              <ChatMessage
                v-for="item in visibleItems"
                :key="item.id"
                :item="item"
                :output="toolOutput(item)"
                @download="demo.download"
              />
              <div v-if="running && !waitingForUser" class="working-notice">
                <span class="typing-dots"><i></i><i></i><i></i></span
                >{{
                  turn?.waitpoint?.kind === "cooperation"
                    ? "Agent 正在等待协作结果"
                    : "Agent 正在处理"
                }}
              </div>
              <div v-if="turn?.status === 'failed'" class="turn-notice">
                这一轮执行失败。{{
                  state.runError || "可以发送新消息，或在调试台查看运行状态。"
                }}
              </div>
              <div v-if="state.artifacts.length" class="artifacts">
                <span class="section-label">交付文件</span
                ><button
                  v-for="path in state.artifacts"
                  :key="path"
                  class="artifact-download"
                  :disabled="!!state.downloading"
                  @click="demo.download(path)"
                >
                  <span class="file-icon"><Download :size="16" /></span
                  ><span
                    ><strong>{{ path.split("/").pop() }}</strong
                    ><small>{{
                      state.downloading === path ? "正在下载…" : "点击下载"
                    }}</small></span
                  ><Download :size="14" />
                </button>
              </div>
            </div>
            <button
              v-if="!state.followScroll"
              class="scroll-to-bottom"
              aria-label="回到最新消息"
              @click="scrollBottom"
            >
              <ArrowDown :size="17" />
            </button>
            <div v-if="waitingForUser" class="waitpoint-panel">
              <strong>{{
                waitpoint.kind === "answer"
                  ? "Agent 需要你的回答"
                  : "工具调用需要确认"
              }}</strong>
              <form @submit.prevent="demo.resume()">
                <template v-if="waitpoint.kind === 'answer'">
                  <template
                    v-for="question in waitpoint.questions"
                    :key="question.question_id"
                  >
                    <label v-if="!questionHasOptions(question)"
                      >{{
                        question.question ||
                        question.title ||
                        question.question_id
                      }}<textarea
                        v-model="state.answers[question.question_id]"
                        required
                        rows="2"
                        :placeholder="question.description || '请输入回答'"
                      ></textarea></label
                    ><fieldset v-else class="question-options">
                      <legend>
                        {{
                          question.question ||
                          question.title ||
                          question.question_id
                        }}
                      </legend>
                      <label
                        v-for="option in question.options"
                        :key="option.value"
                        class="option-choice"
                      >
                        <input
                          v-if="question.multi_select === true"
                          v-model="state.answers[question.question_id]"
                          type="checkbox"
                          :value="option.value"
                        /><input
                          v-else
                          v-model="state.answers[question.question_id]"
                          type="radio"
                          :name="`answer-${question.question_id}`"
                          :value="option.value"
                          required
                          @change="state.otherSelected[question.question_id] = false"
                        /><span
                          >{{ option.label
                          }}<small v-if="option.description">{{
                            option.description
                          }}</small></span
                        >
                      </label>
                      <label v-if="question.allow_other !== false" class="option-choice">
                        <input
                          v-if="question.multi_select === true"
                          v-model="state.otherSelected[question.question_id]"
                          type="checkbox"
                        /><input
                          v-else
                          type="radio"
                          :name="`answer-${question.question_id}`"
                          :checked="state.otherSelected[question.question_id]"
                          @change="state.answers[question.question_id] = ''; state.otherSelected[question.question_id] = true"
                        /><span>其他</span>
                      </label>
                      <label v-if="state.otherSelected[question.question_id]">
                        自行填写
                        <textarea
                          v-model="state.otherText[question.question_id]"
                          required
                          rows="2"
                          placeholder="请输入其他答案"
                        ></textarea>
                      </label>
                    </fieldset>
                  </template>
                </template>
                <template v-else
                  ><label v-for="call in waitpoint.calls" :key="call.call_id"
                    >{{ call.name || call.tool_name || call.call_id }}
                    <pre>{{
                      JSON.stringify(call.args || call.arguments || {}, null, 2)
                    }}</pre>
                    <select v-model="state.decisions[call.call_id]" required>
                      <option value="" disabled>选择处理方式</option>
                      <option
                        v-for="decision in call.allowed_decisions"
                        :key="decision"
                        :value="decision"
                      >
                        {{ decision === "approve" ? "允许" : "拒绝" }}
                      </option>
                    </select></label
                  ></template
                ><button
                  class="primary-button"
                  type="submit"
                  :disabled="state.sending || !!state.pending"
                >
                  提交并继续
                </button>
              </form>
            </div>
            <div v-if="state.snapshot?.yuxi.queue_paused" class="queue-paused">
              <span>后续消息已暂停</span
              ><button
                class="text-button"
                :disabled="
                  state.sending ||
                  !!state.pending ||
                  turn?.cancellation_pending
                "
                @click="demo.continueQueue()"
              >
                继续队列
              </button>
            </div>
            <footer class="composer-area">
              <div class="composer-status">
                <span>{{
                  waitingForUser
                    ? "请先处理上方的回答或审批"
                    : state.snapshot?.yuxi?.queued_input_count
                      ? `${state.snapshot.yuxi.queued_input_count} 条消息排队中`
                      : "文字对话"
                }}</span>
                <div>
                  <button
                    class="phone-new-chat"
                    :disabled="!state.connected || !state.agentId"
                    @click="demo.newThread()"
                  >
                    <Plus :size="12" />新对话</button
                  ><button
                    v-if="running"
                    class="stop-button"
                    :disabled="
                      state.sending ||
                      !!state.pending ||
                      turn?.cancellation_pending
                    "
                    @click="demo.cancel()"
                  >
                    <Square :size="10" fill="currentColor" />停止</button
                  ><span v-else class="enter-hint">Enter 发送</span>
                </div>
              </div>
              <div v-if="state.draftFiles.length || state.draftImages.length" class="composer-status">
                <span v-for="file in state.draftFiles" :key="file.id">{{ file.filename }} <button type="button" :disabled="state.sending || !!state.pending" :aria-label="`移除附件 ${file.filename}`" @click="demo.removeFile(file)"><X :size="12" /></button></span>
                <span v-for="(image, index) in state.draftImages" :key="index">{{ image.name }} <button type="button" :disabled="state.sending || !!state.pending" :aria-label="`移除图片 ${image.name}`" @click="state.draftImages.splice(index, 1)"><X :size="12" /></button></span>
              </div>
              <input ref="attachmentPicker" type="file" multiple hidden @change="demo.addFiles($event.target.files); $event.target.value = ''" />
              <input ref="imagePicker" type="file" multiple accept="image/*" hidden @change="demo.addFiles($event.target.files, true); $event.target.value = ''" />
              <form class="composer" @submit.prevent="demo.send()">
                <button type="button" class="stop-button" aria-label="添加附件" :disabled="blocked || state.uploading" @click="attachmentPicker.click()"><Paperclip :size="16" /></button>
                <button type="button" class="stop-button" aria-label="添加图片" :disabled="blocked || state.uploading" @click="imagePicker.click()"><Image :size="16" /></button>
                <textarea
                  ref="composer"
                  v-model="state.draft"
                  aria-label="消息输入"
                  :placeholder="
                    running ? '继续发消息，Agent 会按顺序处理…' : '发送消息…'
                  "
                  rows="1"
                  :disabled="blocked"
                  @keydown="onKeydown"
                ></textarea
                ><button
                  class="send-button"
                  type="submit"
                  aria-label="发送消息"
                  :disabled="blocked || state.uploading || (!state.draft.trim() && !state.draftImages.length)"
                >
                  <RefreshCw
                    v-if="state.sending"
                    :size="17"
                    class="spin"
                  /><Send v-else :size="18" />
                </button>
              </form>
              <div class="composer-footnote">
                {{ activeUser.name }}<span>·</span>{{ activeApp.name }}
              </div>
            </footer>
          </div>
        </section>
        <p class="device-footnote">
          由 Yuxi Public API 驱动<span>·</span>真实对话，实时响应
        </p>
      </div>
      <dialog
        v-if="workbenchOpen && narrow"
        ref="drawer"
        class="workbench-drawer"
        aria-label="调试工作台抽屉"
        @cancel.prevent="workbenchOpen = false"
        @click.self="workbenchOpen = false"
      >
        <DebugWorkbench :demo="demo" @close="workbenchOpen = false" />
      </dialog>
      <DebugWorkbench
        v-else-if="workbenchOpen"
        :demo="demo"
        @close="workbenchOpen = false"
      />
    </main>
  </div>
</template>
