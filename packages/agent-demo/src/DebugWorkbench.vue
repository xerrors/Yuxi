<script setup>
import { computed, reactive, ref, watch } from "vue";
import {
  X,
  Plus,
  PlugZap,
  Eye,
  EyeOff,
  Users,
  Activity,
  Settings2,
  Trash2,
  RefreshCw,
  Copy,
} from "@lucide/vue";

const props = defineProps({ demo: { type: Object, required: true } });
const emit = defineEmits(["close"]);
const { config, activeApp, state } = props.demo;
const tab = ref("connection");
const showKey = ref(false);
const userName = ref("");
const userId = ref("");
const copied = ref(false);
const form = reactive({ name: "", appId: "", key: "", baseUrl: "" });
watch(
  () => [
    activeApp.value.id,
    activeApp.value.name,
    activeApp.value.appId,
    activeApp.value.key,
    config.baseUrl,
  ],
  () => {
    Object.assign(form, {
      name: activeApp.value.name,
      appId: activeApp.value.appId,
      key: activeApp.value.key,
      baseUrl: config.baseUrl,
    });
    showKey.value = false;
  },
  { immediate: true },
);
const active = computed(() => activeApp.value);

/** 新增本地身份后清空表单。 */
function addUser() {
  if (props.demo.addUser(userName.value, userId.value)) {
    userName.value = "";
    userId.value = "";
  }
}

/** 复制经过脱敏的工作台记录。 */
async function copyLogs() {
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.logs, null, 2));
    copied.value = true;
    setTimeout(() => {
      copied.value = false;
    }, 1500);
  } catch {
    state.error = "当前浏览器不允许复制，请展开事件查看。";
  }
}
</script>

<template>
  <aside class="workbench" aria-label="调试工作台">
    <header class="workbench-head">
      <div>
        <Settings2 :size="17" /><strong>调试工作台</strong
        ><span class="local-tag">LOCAL</span>
      </div>
      <button
        class="icon-button close-workbench"
        aria-label="关闭调试台"
        @click="emit('close')"
      >
        <X :size="18" />
      </button>
    </header>
    <div class="workbench-tabs" role="tablist" aria-label="工作台功能">
      <button
        role="tab"
        :aria-selected="tab === 'connection'"
        :class="{ active: tab === 'connection' }"
        @click="tab = 'connection'"
      >
        <PlugZap :size="14" />连接
      </button>
      <button
        role="tab"
        :aria-selected="tab === 'users'"
        :class="{ active: tab === 'users' }"
        @click="tab = 'users'"
      >
        <Users :size="14" />用户
      </button>
      <button
        role="tab"
        :aria-selected="tab === 'events'"
        :class="{ active: tab === 'events' }"
        @click="tab = 'events'"
      >
        <Activity :size="14" />事件<span
          v-if="state.logs.length"
          class="count"
          >{{ state.logs.length }}</span
        >
      </button>
    </div>
    <div class="workbench-content">
      <div v-if="tab === 'connection'" class="connection-panel">
        <div class="section-label">
          <span>APP 实例</span
          ><button class="text-button" @click="demo.addApp()">
            <Plus :size="13" />新增
          </button>
        </div>
        <div class="app-list">
          <button
            v-for="app in config.apps"
            :key="app.id"
            class="app-option"
            :class="{ selected: active.id === app.id }"
            @click="config.activeAppId = app.id"
          >
            <span class="app-monogram">{{ app.name.slice(0, 1) }}</span
            ><span
              ><strong>{{ app.name }}</strong
              ><small>{{ app.appId || "待识别 APP ID" }}</small></span
            ><span v-if="active.id === app.id" class="selection-dot"></span>
          </button>
        </div>
        <form class="settings-form" @submit.prevent="demo.saveConnection(form)">
          <label
            >服务地址<input
              v-model="form.baseUrl"
              type="url"
              required
              placeholder="http://localhost:5173"
              autocomplete="off"
            /><small>浏览器直接访问该服务。</small></label
          >
          <label
            >实例名称<input
              v-model="form.name"
              maxlength="60"
              placeholder="演示 APP"
          /></label>
          <label
            >APP ID <span class="optional">可选</span
            ><input
              v-model="form.appId"
              maxlength="64"
              placeholder="连接后自动识别"
              autocomplete="off"
            /><small>由 Key 的绑定决定；填写后校验一致性。</small></label
          >
          <label
            >API Key
            <div class="key-input">
              <input
                v-model="form.key"
                :type="showKey ? 'text' : 'password'"
                required
                placeholder="yxkey_…"
                autocomplete="off"
                spellcheck="false"
              /><button
                type="button"
                class="icon-button"
                :aria-label="showKey ? '隐藏 Key' : '显示 Key'"
                @click="showKey = !showKey"
              >
                <EyeOff v-if="showKey" :size="16" /><Eye v-else :size="16" />
              </button></div
          ></label>
          <button
            type="submit"
            class="primary-button"
            :disabled="state.connecting"
          >
            <RefreshCw
              v-if="state.connecting"
              :size="15"
              class="spin"
            /><PlugZap v-else :size="15" />{{
              state.connecting ? "正在连接" : "保存并连接"
            }}
          </button>
        </form>
        <p class="local-note">
          配置仅保存于当前浏览器，包含 API Key。对话记录保存在 Yuxi 服务端。
        </p>
      </div>
      <div v-else-if="tab === 'users'" class="users-panel">
        <div class="section-label">
          <span>{{ active.name }} 的用户</span
          ><span class="subtle">{{ active.users.length }} 位</span>
        </div>
        <div class="user-list">
          <button
            v-for="user in active.users"
            :key="user.id"
            class="user-option"
            :class="{ selected: active.userId === user.id }"
            @click="active.userId = user.id"
          >
            <span class="user-avatar">{{ user.name.slice(0, 1) }}</span
            ><span
              ><strong>{{ user.name }}</strong
              ><small>{{ user.id || "默认身份" }}</small></span
            ><span
              v-if="active.userId === user.id"
              class="selection-dot"
            ></span>
          </button>
        </div>
        <form class="settings-form user-form" @submit.prevent="addUser">
          <strong>新增演示用户</strong
          ><label
            >显示名称<input
              v-model="userName"
              placeholder="例如：小林"
              maxlength="60" /></label
          ><label
            >用户 ID<input
              v-model="userId"
              placeholder="ASCII，例如：customer-01"
              required
              maxlength="128"
              autocomplete="off" /></label
          ><button class="secondary-button" type="submit">
            <Plus :size="15" />新增并切换
          </button>
        </form>
        <p class="local-note">
          用户列表保存在本地。调用时通过 X-End-User-Id
          区分身份，首次请求由服务端创建对应用户。
        </p>
      </div>
      <div v-else class="events-panel">
        <div class="section-label">
          <span>当前会话</span
          ><button
            class="text-button"
            :disabled="!state.threadId"
            @click="demo.refresh()"
          >
            <RefreshCw :size="13" />刷新
          </button>
        </div>
        <dl class="runtime-facts">
          <dt>Thread</dt>
          <dd>{{ state.threadId || "—" }}</dd>
          <dt>Turn</dt>
          <dd>{{ state.snapshot?.yuxi.current_turn?.id || "—" }}</dd>
          <dt>Run</dt>
          <dd>{{ state.snapshot?.yuxi.current_turn?.current_run_id || "—" }}</dd>
          <dt>Result Run</dt>
          <dd>{{ state.snapshot?.yuxi.current_turn?.result_run_id || "—" }}</dd>
          <dt>状态</dt>
          <dd>{{ state.snapshot?.yuxi.current_turn?.status || "idle" }}</dd>
          <dt>事件流</dt>
          <dd>{{ state.stream }}</dd>
        </dl>
        <div class="section-label event-toolbar">
          <span>请求与事件</span>
          <div>
            <button
              class="icon-button"
              :aria-label="copied ? '已复制' : '复制脱敏记录'"
              @click="copyLogs"
            >
              <Copy :size="14" /></button
            ><button
              class="icon-button"
              aria-label="清空记录"
              @click="state.logs = []"
            >
              <Trash2 :size="14" />
            </button>
          </div>
        </div>
        <p v-if="!state.logs.length" class="empty-events">
          连接服务后，调用记录会出现在这里。
        </p>
        <details
          v-for="(entry, index) in state.logs"
          :key="index"
          class="event-entry"
        >
          <summary>
            <span
              class="event-kind"
              :class="{ 'http-kind': entry.kind === 'http' }"
              >{{ entry.kind === "http" ? entry.method : "SSE" }}</span
            ><span class="event-name">{{
              entry.kind === "http" ? entry.path : entry.type
            }}</span
            ><span>{{ entry.status || entry.time }}</span>
          </summary>
          <pre>{{ JSON.stringify(entry, null, 2) }}</pre>
        </details>
      </div>
    </div>
    <footer class="workbench-footer">
      <span class="status-dot" :class="{ online: state.connected }"></span
      >{{ state.connected ? "已连接 Public API" : "尚未连接"
      }}<span>仅本地配置</span>
    </footer>
  </aside>
</template>
