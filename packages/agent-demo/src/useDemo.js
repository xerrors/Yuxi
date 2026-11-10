import { computed, onMounted, onUnmounted, reactive, watch } from "vue";
import { createApi, downloadFilename, validateUserId } from "./api.js";
import {
  loadConfig,
  newApp,
  newId,
  normalizeBaseUrl,
  STORAGE_KEY,
} from "./config.js";
import {
  applyEvent,
  createItems,
  mergeSnapshot,
  orderedItems,
  questionHasOptions,
  readEvents,
} from "./protocol.js";

/** 让重连等待随视图切换一起结束。 */
function waitForReconnect(signal) {
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, 1500);
    if (signal.aborted) finish();
    else signal.addEventListener("abort", finish, { once: true });
  });
}

/** 编排独立 Demo 的身份、会话和公开协议。 */
export function useDemo() {
  const loaded = loadConfig(localStorage);
  const config = reactive(loaded.config);
  const activeApp = computed(() =>
    config.apps.find((app) => app.id === config.activeAppId),
  );
  const state = reactive({
    connected: false,
    connecting: false,
    agents: [],
    agentId: "",
    threads: [],
    hasMore: false,
    loadingThreads: false,
    threadId: "",
    snapshot: null,
    projection: createItems(),
    itemPage: { after: null, hasMore: false, loading: false },
    artifacts: [],
    stream: "idle",
    error: loaded.error,
    runError: "",
    storageError: "",
    sending: false,
    pending: null,
    draft: "",
    draftFiles: [],
    draftImages: [],
    uploading: false,
    logs: [],
    downloading: "",
    answers: {},
    otherSelected: {},
    otherText: {},
    decisions: {},
    followScroll: true,
  });
  let scope = 0;
  let view = 0;
  let listRequest = 0;
  let historyRequest = 0;
  let artifactRequest = 0;
  let identityController;
  let threadController;
  let api;
  let threadApi;
  const items = computed(() => orderedItems(state.projection));
  const turn = computed(() => state.snapshot?.yuxi.current_turn);
  const waitpoint = computed(() =>
    turn.value?.status === "requires_action" ? turn.value.waitpoint : null,
  );
  const waitingForUser = computed(() =>
    ["answer", "approval"].includes(waitpoint.value?.kind),
  );
  const running = computed(() =>
    ["queued", "in_progress"].includes(
      turn.value?.status,
    ),
  );
  const token = () => ({ scope, view });
  const current = (owner) => owner.scope === scope && owner.view === view;

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
      state.storageError = "";
    } catch {
      state.storageError = "浏览器无法保存配置，本次修改仅在当前页面有效。";
    }
  }
  watch(config, persist, { deep: true });

  /** 脱敏后只在内存保留最近的公开调试记录。 */
  function recordLog(entry, key) {
    const clean = JSON.stringify(entry).replaceAll(key.trim(), "[已隐藏]");
    state.logs.unshift({
      ...JSON.parse(clean),
      time: new Date().toLocaleTimeString("zh-CN"),
    });
    state.logs.length = Math.min(state.logs.length, 80);
  }

  function makeApi(signal) {
    const ownerScope = scope;
    const settings = { ...activeApp.value, baseUrl: config.baseUrl };
    return createApi(settings, {
      signal,
      identify: (id) => {
        if (ownerScope === scope && !activeApp.value.appId)
          activeApp.value.appId = id;
      },
      log: (entry) => {
        if (ownerScope !== scope) return;
        recordLog(entry, settings.key);
      },
    });
  }

  function resetThread() {
    view += 1;
    threadController?.abort();
    state.threadId = "";
    state.snapshot = null;
    state.runError = "";
    state.projection = createItems();
    state.itemPage = { after: null, hasMore: false, loading: false };
    state.artifacts = [];
    state.downloading = "";
    state.stream = "idle";
    state.pending = null;
    state.sending = false;
    state.draft = "";
    state.draftFiles = [];
    state.draftImages = [];
    state.uploading = false;
    state.answers = {};
    state.otherSelected = {};
    state.otherText = {};
    state.decisions = {};
    state.followScroll = true;
  }

  function showError(error, owner) {
    if (current(owner) && error.name !== "AbortError")
      state.error = error.message;
  }

  async function connect() {
    scope += 1;
    identityController?.abort();
    resetThread();
    identityController = new AbortController();
    const owner = token();
    state.connected = false;
    state.connecting = false;
    state.agents = [];
    state.agentId = "";
    state.threads = [];
    state.hasMore = false;
    state.loadingThreads = false;
    state.logs = [];
    state.error = "";
    if (!activeApp.value.key.trim()) return;
    state.connecting = true;
    try {
      api = makeApi(identityController.signal);
      const result = await api.listAgents();
      if (!current(owner)) return;
      state.agents = result.data;
      state.agentId = result.data[0]?.id || "";
      state.connected = true;
      if (state.agentId) await loadThreads();
    } catch (error) {
      showError(error, owner);
    } finally {
      if (owner.scope === scope) state.connecting = false;
    }
  }

  async function loadThreads(more = false) {
    if (!api || !state.agentId || state.loadingThreads) return;
    const ownerScope = scope;
    const agentId = state.agentId;
    const request = ++listRequest;
    const stillSelected = () =>
      ownerScope === scope &&
      agentId === state.agentId &&
      request === listRequest;
    const after = more ? state.threads.at(-1)?.id : undefined;
    state.loadingThreads = true;
    try {
      const result = await api.listThreads(agentId, after);
      if (!stillSelected()) return;
      state.threads = more ? [...state.threads, ...result.data] : result.data;
      state.hasMore = result.has_more;
    } catch (error) {
      if (stillSelected() && error.name !== "AbortError")
        state.error = error.message;
    } finally {
      if (stillSelected()) state.loadingThreads = false;
    }
  }

  async function selectAgent(id) {
    resetThread();
    state.agentId = id;
    state.error = "";
    state.threads = [];
    state.loadingThreads = false;
    await loadThreads();
  }

  function syncWaitpoint(previousId) {
    const point = waitpoint.value;
    if (point?.id === previousId) return;
    state.answers = Object.fromEntries(
      (point?.questions || []).map((question) => [
        question.question_id,
        questionHasOptions(question) && question.multi_select === true ? [] : "",
      ]),
    );
    state.otherSelected = {};
    state.otherText = {};
    state.decisions = Object.fromEntries(
      (point?.calls || []).map((call) => [call.call_id, ""]),
    );
  }

  async function refreshHistory(owner = token(), { more = false, turnId } = {}) {
    if (!current(owner)) return;
    const request = ++historyRequest;
    const previousIds = new Set(Object.keys(state.projection.items));
    const previousTurnId = turnId || state.snapshot?.yuxi.current_turn?.id;
    const session = await threadApi.session(state.threadId);
    if (!current(owner) || request !== historyRequest) return;
    const previous = waitpoint.value?.id;
    let after = more ? state.itemPage.after : undefined;
    let page;
    do {
      page = await threadApi.items(state.threadId, after);
      if (!current(owner) || request !== historyRequest) return;
      mergeSnapshot(state.projection, page.data);
      after = page.last_id;
    } while (page.has_more && !more && previousIds.size && !page.data.some((item) => previousIds.has(item.id)));
    if (more || !previousIds.size || !page.has_more) {
      state.itemPage.after = page.last_id;
      state.itemPage.hasMore = page.has_more;
    }
    const id = previousTurnId || session.yuxi.current_turn?.id;
    if (id && !more) {
      const target = await threadApi.turn(state.threadId, id);
      if (!current(owner) || request !== historyRequest) return;
      mergeSnapshot(state.projection, target.yuxi.output);
      state.runError = target.error?.message || "";
      if (session.yuxi?.current_turn?.id === target.id) {
        session.yuxi.current_turn.cancellation_pending = Boolean(target.yuxi?.cancellation_pending);
      }
      if (turnId || ["queued", "in_progress", "requires_action"].includes(target.status)) {
        let cursor;
        do {
          const page = await threadApi.items(state.threadId, cursor, id);
          if (!current(owner) || request !== historyRequest) return;
          mergeSnapshot(state.projection, page.data);
          cursor = page.has_more ? page.last_id : null;
        } while (cursor);
      }
    }
    state.snapshot = session;
    syncWaitpoint(previous);
    const existing = state.threads.findIndex(
      (thread) => thread.id === state.threadId,
    );
    if (existing >= 0) state.threads[existing] = session;
    else state.threads.unshift(session);
  }

  async function refreshArtifacts(owner = token()) {
    if (!current(owner)) return;
    const request = ++artifactRequest;
    try {
      const result = await threadApi.state(state.threadId);
      if (current(owner) && request === artifactRequest)
        state.artifacts = result.agent_state.artifacts || [];
    } catch (error) {
      showError(error, owner);
    }
  }

  async function observe(owner) {
    const client = threadApi;
    const id = state.threadId;
    const signal = threadController.signal;
    let cursor;
    while (current(owner) && !signal.aborted) {
      try {
        const response = await client.events(id, cursor);
        if (!current(owner)) {
          await response.body?.cancel();
          return;
        }
        state.stream = "live";
        await readEvents(response, async (event, nextCursor) => {
          if (!current(owner)) return;
          // 续订 cursor 与逻辑 event_id 分别拥有恢复与去重职责。
          if (event.session_id && event.session_id !== id)
            throw new Error("事件流返回了其他 Thread 的事件");
          recordLog(
            { kind: "event", type: event.type, data: event },
            activeApp.value.key,
          );
          if (event.type === "yuxi.session.resync") {
            await refreshHistory(owner, { turnId: event.turn_id });
          } else {
            applyEvent(state.projection, event);
            if (
              /^agent\.session\.turn\.(created|in_progress|completed|failed|cancelled)$/.test(
                event.type,
              ) ||
              ["yuxi.session.turn.waiting", "yuxi.session.turn.state"].includes(
                event.type,
              )
            ) {
              await refreshHistory(owner, { turnId: event.turn_id });
              await refreshArtifacts(owner);
            }
          }
          cursor = nextCursor || cursor;
        });
      } catch (error) {
        if (!current(owner) || signal.aborted) return;
        if (error.status >= 400 && error.status < 500) {
          state.stream = "error";
          state.error = error.message;
          return;
        }
        state.stream = "reconnecting";
      }
      if (!current(owner) || signal.aborted) return;
      state.stream = "reconnecting";
      await waitForReconnect(signal);
      try {
        await refreshHistory(owner);
      } catch (error) {
        showError(error, owner);
      }
    }
  }

  async function selectThread(id) {
    resetThread();
    state.threadId = id;
    state.error = "";
    threadController = new AbortController();
    threadApi = makeApi(threadController.signal);
    const owner = token();
    try {
      state.stream = "connecting";
      await refreshHistory(owner);
      if (!current(owner)) return;
      await refreshArtifacts(owner);
      if (current(owner)) void observe(owner);
    } catch (error) {
      showError(error, owner);
      if (current(owner)) state.stream = "error";
    }
  }

  async function runCommand(command) {
    if (state.sending) return;
    const owner = token();
    const client = state.threadId ? threadApi : api;
    state.sending = true;
    state.error = "";
    state.pending = command;
    try {
      let result;
      // 同一命令重试先查原回执；创建回执通过重放相同创建请求恢复。
      if (command.attempted && command.path !== "/sessions") {
        try { result = await client.receipt(state.threadId, command.key); }
        catch (error) { if (error.status !== 404) throw error; }
      }
      command.attempted = true;
      result ||= await client.request(command.path, {
        method: "POST", body: command.body, idempotencyKey: command.key,
      });
      if (!current(owner)) return;
      state.pending = null;
      if (command.fileIds) state.draftFiles = state.draftFiles.filter((file) => !command.fileIds.includes(file.id));
      if (command.images) state.draftImages = state.draftImages.filter((image) => !command.images.includes(image));
      if (command.text && state.draft === command.text) state.draft = "";
      if (command.path === "/sessions") {
        await selectThread(result.id);
      } else {
        await refreshHistory(owner);
        await refreshArtifacts(owner);
      }
    } catch (error) {
      if (!current(owner)) return;
      // 网络和服务端失败可能已提交；手动重试复用同一意图与幂等键。
      if (error.status >= 400 && error.status < 500) state.pending = null;
      showError(error, owner);
    } finally {
      if (current(owner)) state.sending = false;
    }
  }

  function submitEvent(event) {
    if (state.pending || !state.threadId || state.sending) return;
    return runCommand({
      path: `/sessions/${encodeURIComponent(state.threadId)}/events`,
      body: { events: [event] },
      key: newId(),
    });
  }

  function send() {
    const text = state.draft.trim();
    if (
      (!text && !state.draftImages.length) ||
      state.uploading ||
      !state.connected ||
      !state.agentId ||
      waitingForUser.value ||
      state.pending ||
      state.sending
    )
      return;
    const images = [...state.draftImages];
    const fileIds = state.draftFiles.map((file) => file.id);
    const content = [...(text ? [{ type: "input_text", text }] : []), ...images.map((image) => ({ type: "input_image", image_url: image.url }))];
    const input = [{ role: "user", content }];
    const command = { text: state.draft, key: newId(), fileIds, images };
    if (state.threadId) {
      command.path = `/sessions/${encodeURIComponent(state.threadId)}/events`;
      command.body = {
        events: [
          {
            type: "agent.session.input.message",
            input,
            yuxi: { mode: "follow_up", attachment_file_ids: fileIds },
          },
        ],
      };
    } else {
      command.path = "/sessions";
      command.body = {
        agent_id: state.agentId,
        title: text.slice(0, 40),
        input,
        yuxi: { attachment_file_ids: fileIds },
      };
    }
    return runCommand(command);
  }

  async function addFiles(files, imageInput = false) {
    if (!api || state.uploading || state.sending || state.pending) return;
    const owner = token();
    state.uploading = true;
    state.error = "";
    try {
      for (const file of files) {
        if (imageInput) {
          const url = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error("图片读取失败"));
            reader.readAsDataURL(file);
          });
          if (current(owner)) state.draftImages.push({ name: file.name, url });
        } else {
          const draft = await api.uploadFile(file);
          if (current(owner)) state.draftFiles.push(draft);
        }
      }
    } catch (error) {
      showError(error, owner);
    } finally {
      if (current(owner)) state.uploading = false;
    }
  }

  async function removeFile(file) {
    const owner = token();
    try {
      await api.deleteFile(file.id);
      if (current(owner)) state.draftFiles = state.draftFiles.filter((item) => item.id !== file.id);
    } catch (error) {
      showError(error, owner);
    }
  }

  function resume() {
    const point = waitpoint.value;
    if (!point) return;
    let response;
    if (point.kind === "answer") {
      const answers = [];
      for (const question of point.questions) {
        const value = state.answers[question.question_id];
        const hasOptions = questionHasOptions(question);
        if (hasOptions && question.allow_other !== false && state.otherSelected[question.question_id]) {
          const text = (state.otherText[question.question_id] || "").trim();
          if (!text) return;
          const selected = question.multi_select === true && Array.isArray(value) ? value : [];
          answers.push({ question_id: question.question_id, answer: { type: "other", text, selected } });
        } else if (hasOptions && question.multi_select === true) {
          if (!Array.isArray(value) || !value.length) return;
          answers.push({ question_id: question.question_id, answer: value });
        } else if (typeof value !== "string" || !value.trim()) {
          return;
        } else {
          answers.push({ question_id: question.question_id, answer: value });
        }
      }
      response = { type: "answer", answers };
    } else if (point.kind === "approval") {
      const decisions = point.calls.map((call) => ({
        call_id: call.call_id,
        decision: state.decisions[call.call_id],
      }));
      if (decisions.some((decision) => !decision.decision)) return;
      response = { type: "approval", decisions };
    } else return;
    return submitEvent({
      type: "yuxi.session.input.resume",
      turn_id: turn.value.id,
      waitpoint_id: point.id,
      response,
    });
  }

  async function download(path) {
    if (state.downloading) return;
    const owner = token();
    state.downloading = path;
    try {
      const response = await threadApi.download(state.threadId, path);
      const blob = await response.blob();
      if (!current(owner)) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = downloadFilename(
        response.headers.get("Content-Disposition"),
        path,
      );
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      showError(error, owner);
    } finally {
      if (current(owner)) state.downloading = "";
    }
  }

  function saveConnection(form) {
    try {
      config.baseUrl = normalizeBaseUrl(form.baseUrl);
      activeApp.value.name = form.name.trim() || "演示 APP";
      activeApp.value.key = form.key.trim();
      activeApp.value.appId = form.appId.trim();
      persist();
      void connect();
    } catch (error) {
      state.error = error.message;
    }
  }

  function addApp() {
    const app = newApp(config.apps.length + 1);
    config.apps.push(app);
    config.activeAppId = app.id;
  }

  function addUser(name, id) {
    try {
      validateUserId(id);
    } catch (error) {
      state.error = error.message;
      return false;
    }
    const identity = id.trim();
    if (!identity || identity.length > 128 || identity === "__default__") {
      state.error = "用户 ID 需要 1–128 个字符，且不能使用保留 ID __default__";
      return false;
    }
    if (activeApp.value.users.some((user) => user.id === identity)) {
      state.error = "这个用户 ID 已存在";
      return false;
    }
    activeApp.value.users.push({ id: identity, name: name.trim() || identity });
    activeApp.value.userId = identity;
    return true;
  }

  watch(
    [() => config.activeAppId, () => activeApp.value.userId],
    () => void connect(),
  );
  onMounted(() => {
    if (activeApp.value.key) void connect();
  });
  onUnmounted(() => {
    identityController?.abort();
    threadController?.abort();
  });

  return {
    addFiles,
    removeFile,
    config,
    activeApp,
    state,
    items,
    turn,
    waitpoint,
    running,
    waitingForUser,
    connect,
    saveConnection,
    addApp,
    addUser,
    loadThreads,
    loadEarlier: async () => {
      if (state.itemPage.loading) return;
      const owner = token();
      state.itemPage.loading = true;
      try { await refreshHistory(owner, { more: true }); }
      catch (error) { showError(error, owner); }
      finally { if (current(owner)) state.itemPage.loading = false; }
    },
    selectAgent,
    selectThread,
    newThread: resetThread,
    send,
    resume,
    download,
    retry: () => state.pending && runCommand(state.pending),
    cancel: () =>
      submitEvent({
        type: "agent.session.input.cancel",
        yuxi: {
          turn_id: turn.value.id,
          expected_run_id: turn.value.current_run_id,
        },
      }),
    continueQueue: () => submitEvent({ type: "yuxi.session.input.continue" }),
    refresh: async () => {
      const owner = token();
      if (!state.threadId) return;
      try {
        await refreshHistory(owner);
        await refreshArtifacts(owner);
      } catch (error) {
        showError(error, owner);
      }
    },
  };
}
