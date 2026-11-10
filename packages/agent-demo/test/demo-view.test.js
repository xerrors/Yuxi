import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { createServer } from "vite";
import vue from "@vitejs/plugin-vue";

globalThis.window = {
  innerWidth: 800,
  addEventListener() {},
  removeEventListener() {},
};
globalThis.document = { activeElement: null };

const mockDemo = `
import { computed, reactive } from "vue";

export const state = reactive({
  connected: true,
  connecting: false,
  agents: [{ id: "chat", name: "Agent", description: "聊天" }],
  agentId: "chat",
  threads: [],
  hasMore: false,
  loadingThreads: false,
  threadId: "thread-1",
  snapshot: null,
  itemPage: { hasMore: false, loading: false },
  artifacts: [],
  stream: "idle",
  error: "",
  runError: "",
  storageError: "",
  sending: false,
  pending: null,
  draft: "",
  draftFiles: [],
  draftImages: [],
  uploading: false,
  downloading: "",
  answers: {},
  otherSelected: {},
  otherText: {},
  decisions: {},
  followScroll: true,
});
globalThis.__agentDemoView = state;

export function useDemo() {
  const activeApp = computed(() => ({
    name: "Alpha",
    userId: "",
    users: [{ id: "", name: "默认用户" }],
  }));
  const turn = computed(() => state.snapshot?.yuxi.current_turn ?? null);
  const waitpoint = computed(() =>
    turn.value?.status === "requires_action" ? turn.value.waitpoint : null,
  );
  const waitingForUser = computed(() =>
    ["answer", "approval"].includes(waitpoint.value?.kind),
  );
  const running = computed(() =>
    ["queued", "in_progress"].includes(turn.value?.status),
  );
  const noop = () => {};
  return {
    config: { layout: "phone" },
    activeApp,
    state,
    items: computed(() => []),
    turn,
    running,
    waitpoint,
    waitingForUser,
    addFiles: noop,
    removeFile: noop,
    loadThreads: noop,
    loadEarlier: noop,
    selectAgent: noop,
    newThread: noop,
    send: noop,
    resume: noop,
    download: noop,
    retry: noop,
    cancel: noop,
    continueQueue: noop,
  };
}
`;

function panel(html) {
  const start = html.indexOf('class="waitpoint-panel"');
  if (start < 0) return "";
  const stops = ["queue-paused", "composer-area"]
    .map((name) => html.indexOf(name, start))
    .filter((index) => index >= 0);
  return html.slice(start, stops.length ? Math.min(...stops) : undefined);
}

function composer(html) {
  return html.match(/<textarea\b[^>]*aria-label="消息输入"[^>]*>/u)?.[0] || "";
}

test("暂停不禁用发送；有选项渲染选项，无选项保留文本框，审批 select 保留", async () => {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const server = await createServer({
    root,
    configFile: false,
    appType: "custom",
    logLevel: "error",
    plugins: [
      {
        name: "mock-demo",
        enforce: "pre",
        resolveId(id, importer) {
          if (importer?.endsWith("App.vue") && id.endsWith("useDemo.js")) {
            return "\0mock-use-demo";
          }
          return null;
        },
        load(id) {
          if (id === "\0mock-use-demo") return mockDemo;
          return null;
        },
      },
      vue(),
    ],
    server: { middlewareMode: true },
  });
  try {
    const { default: App } = await server.ssrLoadModule("/src/App.vue");
    const view = globalThis.__agentDemoView;
    const render = () => renderToString(createSSRApp(App));

    view.snapshot = {
      yuxi: {
        queue_paused: true,
        queued_input_count: 2,
        title: "对话",
        current_turn: { id: "turn-1", status: "cancelled", waitpoint: null },
      },
    };
    view.draft = "只跑这一条";
    view.connected = true;
    let html = await render();
    assert.match(html, /继续队列/);
    assert.equal(composer(html).includes("disabled"), false);
    assert.equal(
      html.match(/<button\b[^>]*aria-label="发送消息"[^>]*>/u)[0].includes("disabled"),
      false,
    );

    view.connected = false;
    html = await render();
    assert.equal(composer(html).includes("disabled"), true);

    view.connected = true;
    view.snapshot.yuxi.current_turn = {
      id: "turn-1",
      status: "requires_action",
      waitpoint: {
        id: "wait-text",
        kind: "answer",
        questions: [
          { question_id: "q-text", question: "你的目标是什么？" },
          { question_id: "q-empty", question: "补充", options: [] },
        ],
      },
    };
    view.answers = { "q-text": "", "q-empty": "" };
    html = await render();
    const textPanel = panel(html);
    assert.match(textPanel, /你的目标是什么？/);
    assert.match(textPanel, /<textarea\b/u);
    assert.doesNotMatch(textPanel, /type="radio"/u);
    assert.equal(composer(html).includes("disabled"), true);

    view.snapshot.yuxi.current_turn.waitpoint = {
      id: "wait-choice",
      kind: "answer",
      questions: [
        {
          question_id: "q-choice",
          question: "选择交付格式",
          multi_select: false,
          options: [
            { label: "Markdown", value: "markdown", description: "便于继续编辑" },
            { label: "PDF", value: "pdf" },
          ],
        },
      ],
    };
    view.answers = { "q-choice": "" };
    html = await render();
    const choicePanel = panel(html);
    assert.match(choicePanel, /选择交付格式/);
    assert.match(choicePanel, /type="radio"/u);
    assert.match(choicePanel, /value="markdown"/u);
    assert.match(choicePanel, /value="pdf"/u);
    assert.match(choicePanel, /便于继续编辑/);
    assert.match(choicePanel, /其他/);
    assert.doesNotMatch(choicePanel, /<textarea\b/u);

    view.otherSelected = { "q-choice": true };
    html = await render();
    assert.match(panel(html), /请输入其他答案/);

    view.snapshot.yuxi.current_turn.waitpoint.questions[0].allow_other = false;
    view.otherSelected = {};
    html = await render();
    assert.doesNotMatch(panel(html), /其他/);

    view.snapshot.yuxi.current_turn.waitpoint = {
      id: "wait-multi",
      kind: "answer",
      questions: [
        {
          question_id: "q-multi",
          question: "选择读者",
          multi_select: true,
          options: [
            { label: "开发", value: "dev" },
            { label: "管理者", value: "lead" },
          ],
        },
      ],
    };
    view.answers = { "q-multi": [] };
    html = await render();
    const multiPanel = panel(html);
    assert.match(multiPanel, /type="checkbox"/u);
    assert.match(multiPanel, /value="dev"/u);
    assert.match(multiPanel, /value="lead"/u);
    assert.doesNotMatch(multiPanel, /type="radio"/u);

    view.snapshot.yuxi.current_turn.waitpoint = {
      id: "wait-approval",
      kind: "approval",
      calls: [
        {
          call_id: "call-1",
          name: "write_file",
          allowed_decisions: ["approve", "reject"],
        },
      ],
    };
    view.decisions = { "call-1": "" };
    html = await render();
    const approvalPanel = panel(html);
    assert.match(approvalPanel, /<select\b/u);
    assert.match(approvalPanel, /允许/);
    assert.match(approvalPanel, /拒绝/);
    assert.doesNotMatch(approvalPanel, /type="radio"/u);
    assert.doesNotMatch(approvalPanel, /type="checkbox"/u);
  } finally {
    await server.close();
  }
});
