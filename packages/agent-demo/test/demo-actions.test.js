import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { createSSRApp, h } from "vue";
import { renderToString } from "vue/server-renderer";
import { STORAGE_KEY } from "../src/config.js";
import { useDemo } from "../src/useDemo.js";

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
  };
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** 用内存身份和假 HTTP 驱动真实 send / resume，不启动浏览器。 */
async function openPausedThread() {
  const requests = [];
  const snapshot = {
    id: "thread-1",
    object: "agent.session",
    yuxi: {
      title: "历史对话",
      queue_paused: true,
      queued_input_count: 1,
      current_turn: {
        id: "turn-1",
        status: "cancelled",
        current_run_id: "run-1",
        waitpoint: null,
      },
    },
  };
  globalThis.localStorage = memoryStorage();
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      baseUrl: "http://fixture.test",
      layout: "phone",
      activeAppId: "alpha",
      apps: [
        {
          id: "alpha",
          name: "Alpha",
          appId: "",
          key: "test-key",
          userId: "",
          users: [{ id: "", name: "默认用户" }],
        },
      ],
    }),
  );
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ method, path: parsed.pathname, body });
    if (parsed.pathname.endsWith("/events")) {
      return jsonResponse({ detail: "不再订阅" }, 400);
    }
    if (parsed.pathname === "/api/v1/agents/") {
      return jsonResponse({ data: [{ id: "chat", name: "Agent" }] });
    }
    if (parsed.pathname === "/api/v1/agents/sessions" && method === "GET") {
      return jsonResponse({ data: [], has_more: false });
    }
    if (parsed.pathname === "/api/v1/agents/sessions/thread-1" && method === "GET") {
      return jsonResponse(structuredClone(snapshot));
    }
    if (parsed.pathname.endsWith("/items")) {
      return jsonResponse({ data: [], has_more: false, last_id: null });
    }
    if (parsed.pathname.includes("/turns/")) {
      const turn = snapshot.yuxi.current_turn;
      return jsonResponse({
        id: turn.id,
        status: turn.status,
        error: null,
        yuxi: { output: null, cancellation_pending: false },
      });
    }
    if (parsed.pathname.endsWith("/state")) {
      return jsonResponse({ agent_state: { artifacts: [] } });
    }
    if (method === "POST") {
      return jsonResponse(
        {
          object: "yuxi.session.event.accepted",
          event_id: "evt",
          session_id: "thread-1",
          input_id: "input-1",
          status: "accepted",
        },
        202,
      );
    }
    return jsonResponse({ detail: parsed.pathname }, 500);
  };
  let demo;
  await renderToString(
    createSSRApp({
      setup() {
        demo = useDemo();
        return () => h("div");
      },
    }),
  );
  await demo.connect();
  await demo.selectThread("thread-1");
  await new Promise((resolve) => setTimeout(resolve, 20));
  return { demo, requests, snapshot };
}

function messages(requests) {
  return requests.filter(
    (request) => request.body?.events?.[0]?.type === "agent.session.input.message",
  );
}

function resumes(requests) {
  return requests.filter(
    (request) => request.body?.events?.[0]?.type === "yuxi.session.input.resume",
  );
}

describe("公开输入", { concurrency: 1 }, () => {
test("暂停时 send 仍提交原来的 follow_up，其他阻止条件保留", async () => {
  const { demo, requests } = await openPausedThread();
  assert.equal(demo.state.snapshot.yuxi.queue_paused, true);
  assert.equal(demo.state.connected, true);
  demo.state.draft = "只跑这一条";
  await demo.send();
  assert.equal(messages(requests).length, 1);
  assert.deepEqual(messages(requests)[0].body.events[0], {
    type: "agent.session.input.message",
    input: [
      {
        role: "user",
        content: [{ type: "input_text", text: "只跑这一条" }],
      },
    ],
    yuxi: { mode: "follow_up", attachment_file_ids: [] },
  });

  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "requires_action",
    current_run_id: "run-1",
    waitpoint: {
      id: "wait-1",
      kind: "answer",
      questions: [{ question_id: "q-1", question: "还不能发" }],
    },
  };
  demo.state.draft = "等待中不应发送";
  await demo.send();
  demo.state.uploading = true;
  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "cancelled",
    waitpoint: null,
  };
  demo.state.draft = "上传中不应发送";
  await demo.send();
  demo.state.uploading = false;
  demo.state.connected = false;
  demo.state.draft = "未连接不应发送";
  await demo.send();
  assert.equal(messages(requests).length, 1);
});

test("有 options 时提交所选 value，没有 options 时提交文本", async () => {
  const { demo, requests } = await openPausedThread();
  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "requires_action",
    current_run_id: "run-1",
    waitpoint: {
      id: "wait-text",
      kind: "answer",
      questions: [
        { question_id: "q-text", question: "你的目标是什么？" },
        { question_id: "q-empty", question: "补充", options: [] },
      ],
    },
  };
  demo.state.answers = { "q-text": "展示对话能力", "q-empty": "手写" };
  await demo.resume();
  assert.deepEqual(resumes(requests).at(-1).body.events[0].response, {
    type: "answer",
    answers: [
      { question_id: "q-text", answer: "展示对话能力" },
      { question_id: "q-empty", answer: "手写" },
    ],
  });

  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-choice",
      kind: "answer",
      questions: [
        {
          question_id: "q-choice",
          question: "选择交付格式",
          options: [
            { label: "Markdown", value: "markdown", description: "便于继续编辑" },
            { label: "PDF", value: "pdf" },
          ],
          multi_select: false,
        },
      ],
    },
  };
  demo.state.answers = { "q-choice": "markdown" };
  await demo.resume();
  assert.deepEqual(resumes(requests).at(-1).body.events[0].response, {
    type: "answer",
    answers: [{ question_id: "q-choice", answer: "markdown" }],
  });

  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "requires_action",
    current_run_id: "run-1",
    waitpoint: {
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
    },
  };
  demo.state.answers = { "q-multi": [] };
  await demo.resume();
  assert.equal(resumes(requests).at(-1).body.events[0].waitpoint_id, "wait-choice");
  demo.state.answers = { "q-multi": ["dev", "lead"] };
  await demo.resume();
  assert.deepEqual(resumes(requests).at(-1).body.events[0], {
    type: "yuxi.session.input.resume",
    turn_id: "turn-1",
    waitpoint_id: "wait-multi",
    response: {
      type: "answer",
      answers: [{ question_id: "q-multi", answer: ["dev", "lead"] }],
    },
  });

  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "requires_action",
    current_run_id: "run-1",
    waitpoint: {
      id: "wait-approval",
      kind: "approval",
      calls: [
        {
          call_id: "call-1",
          name: "write_file",
          allowed_decisions: ["approve", "reject"],
        },
      ],
    },
  };
  demo.state.decisions = { "call-1": "approve" };
  await demo.resume();
  assert.deepEqual(resumes(requests).at(-1).body.events[0].response, {
    type: "approval",
    decisions: [{ call_id: "call-1", decision: "approve" }],
  });
});

test("选择题可提交其他答案，多选保留已选项且禁止空文本", async () => {
  const { demo, requests } = await openPausedThread();
  demo.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-other",
      kind: "answer",
      questions: [
        { question_id: "single", options: [{ label: "选项", value: "a" }] },
        { question_id: "multi", multi_select: true, options: [{ label: "选项", value: "a" }] },
        { question_id: "restricted", allow_other: false, options: [{ label: "选项", value: "b" }] },
      ],
    },
  };
  demo.state.answers = { single: "", multi: ["a"], restricted: "b" };
  demo.state.otherSelected = { single: true, multi: true, restricted: true };
  demo.state.otherText = { single: "其他单选", multi: "  ", restricted: "不应提交" };
  await demo.resume();
  assert.equal(resumes(requests).length, 0);

  demo.state.otherText.multi = "  其他多选  ";
  await demo.resume();
  assert.deepEqual(resumes(requests).at(-1).body.events[0].response, {
    type: "answer",
    answers: [
      { question_id: "single", answer: { type: "other", text: "其他单选", selected: [] } },
      { question_id: "multi", answer: { type: "other", text: "其他多选", selected: ["a"] } },
      { question_id: "restricted", answer: "b" },
    ],
  });
});
});
