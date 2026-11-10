import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { networkInterfaces } from "node:os";

const config = (baseUrl) => ({
  baseUrl,
  layout: "phone",
  activeAppId: "alpha",
  apps: [
    {
      id: "alpha",
      name: "Alpha APP",
      appId: "alpha",
      key: "test-alpha-key",
      userId: "",
      users: [
        { id: "", name: "默认用户" },
        { id: "customer-2", name: "小林" },
      ],
    },
    {
      id: "beta",
      name: "Beta APP",
      appId: "beta",
      key: "test-beta-key",
      userId: "",
      users: [{ id: "", name: "默认用户" }],
    },
  ],
});
const message = (id, role, text, status = "completed") => ({
  id,
  type: "message",
  role,
  status,
  phase: role === "assistant" && status === "completed" ? "final_answer" : null,
  content: [{ type: role === "user" ? "input_text" : "output_text", text }],
  yuxi: {
    message_id: Number(id.split("-").at(-1)),
    run_id: role === "assistant" ? "run-1" : null,
    created_at: "2026-10-08T09:00:00Z",
  },
});

/** 用独立 HTTP fixture 验证浏览器实际发送的协议与渲染结果。 */
async function fixture() {
  const requests = [];
  const streams = new Set();
  const state = {
    snapshot: {
      id: "thread-1", object: "agent.session", status: "idle",
      agent: { id: "chat", model: "test:model" }, created_at: 1791446400, last_active_at: 1791446400,
      yuxi: { title: "历史对话", current_turn: null, queued_input_count: 0, queue_paused: false, archived: false },
    },
    items: [
      message("input-1", "user", "历史问题"),
      message("output-2", "assistant", "历史回答"),
    ],
    runs: [],
    artifacts: [],
    failNext: false,
    failNextEvent: false,
    delayAlpha: false,
    oldHistoryDelay: false,
    eventSeq: 0,
    subscriptionCount: 0,
  };
  const receipts = new Map();
  const server = createServer(async (req, res) => {
    res.setHeader(
      "Access-Control-Allow-Origin",
      req.headers.origin || "http://localhost:5180",
    );
    res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
    res.setHeader(
      "Access-Control-Allow-Headers",
      "Authorization,Content-Type,Idempotency-Key,X-End-User-Id,Last-Event-ID",
    );
    res.setHeader(
      "Access-Control-Expose-Headers",
      "X-App-Id,Content-Disposition",
    );
    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }
    const app =
      req.headers.authorization === "Bearer test-alpha-key" ? "alpha" : "beta";
    const user = req.headers["x-end-user-id"] || "";
    res.setHeader("X-App-Id", app);
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const multipart = req.headers["content-type"]?.startsWith("multipart/form-data");
    const body = raw && !multipart ? JSON.parse(raw) : undefined;
    requests.push({
      method: req.method,
      path: req.url,
      headers: req.headers,
      body,
      raw,
    });
    const url = new URL(req.url, "http://fixture");
    const json = (value, status = 200) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(value));
    };
    if (url.pathname === "/api/v1/agents/files" && req.method === "POST") {
      json({ id: "1234567890abcdef1234567890abcdef", object: "file", filename: "draft.txt", bytes: 11, mime_type: "text/plain", status: "draft", created_at: 1791446400, expires_at: 1791532800 }, 201);
    } else if (url.pathname === "/api/v1/agents/") {
      if (state.delayAlpha && app === "alpha")
        await new Promise((resolve) => setTimeout(resolve, 450));
      json({
        data: [
          {
            id: "chat",
            name: `${app} Agent`,
            description: "可以聊天和调用工具",
          },
          { id: "research", name: "研究 Agent", description: "研究问题" },
        ],
      });
    } else if (
      url.pathname === "/api/v1/agents/sessions" &&
      req.method === "GET"
    ) {
      json({ object: "list", data: app === "alpha" && !user ? [state.snapshot] : [], first_id: "thread-1", last_id: "thread-1", has_more: false });
    } else if (url.pathname.endsWith("/items")) {
      const items = structuredClone(state.items).reverse();
      const start = url.searchParams.get("after") ? items.findIndex(item => item.id === url.searchParams.get("after")) + 1 : 0;
      const limit = Number(url.searchParams.get("limit") || 20);
      const data = items.slice(start, start + limit);
      if (state.oldHistoryDelay) {
        state.oldHistoryDelay = false;
        await new Promise(resolve => setTimeout(resolve, 450));
      }
      json({ object: "list", data, first_id: data[0]?.id || null, last_id: data.at(-1)?.id || null, has_more: items.length > start + limit, yuxi: { runs: state.runs } });
    } else if (url.pathname.includes("/turns/")) {
      const turn = state.snapshot.yuxi.current_turn;
      json({ id: url.pathname.split("/").at(-1), status: turn?.status, error: state.runs.find(run => run.status === "failed") ? { message: state.runs.find(run => run.status === "failed").error_message } : null,
        yuxi: { ...turn, current_run_id: turn?.current_run_id, runs: state.runs,
          output: turn?.status === "completed" ? state.items.filter(item => item.phase === "final_answer") : null } });
    } else if (url.pathname.endsWith("/receipt")) {
      const receipt = receipts.get(url.searchParams.get("idempotency_key"));
      json(receipt?.yuxi?.receipt || receipt || { detail: "回执不存在" }, receipt ? 200 : 404);
    } else if (url.pathname === "/api/v1/agents/sessions/thread-1" && req.method === "GET") {
      json(structuredClone(state.snapshot));
    } else if (url.pathname.endsWith("/state")) {
      json({ agent_state: { artifacts: state.artifacts } });
    } else if (url.pathname.endsWith("/events") && req.method === "GET") {
      state.subscriptionCount += 1;
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write(": connected\n\n");
      streams.add(res);
      res.on("close", () => streams.delete(res));
      if (state.snapshot.yuxi.current_turn?.status === "in_progress") {
        const item = state.items.find((entry) => entry.role === "assistant");
        emit({ type: "agent.session.turn.item.added", item });
        emit({
          type: "agent.session.turn.output_text.delta",
          item_id: item.id,
          content_index: 0,
          delta: "你好，",
        });
      }
    } else if (
      url.pathname === "/api/v1/agents/sessions" &&
      req.method === "POST"
    ) {
      const key = req.headers["idempotency-key"];
      if (!receipts.has(key)) {
        state.snapshot = {
          ...state.snapshot,
          status: "in_progress",
          yuxi: { ...state.snapshot.yuxi, title: body.title,
          current_turn: {
            id: "turn-1",
            current_run_id: "run-1",
            status: "in_progress",
            result_run_id: null,
          } },
        };
        state.items = [
          message("input-1", "user", body.input[0].content[0].text),
          message("output-2", "assistant", "", "in_progress"),
        ];
        receipts.set(key, { ...state.snapshot, yuxi: { ...state.snapshot.yuxi, receipt: { object: "yuxi.session.event.accepted", event_id: key, session_id: "thread-1", input_id: "input-1", turn_id: null, run_id: null, status: "accepted" } } });
      }
      if (state.failNext) {
        state.failNext = false;
        json({ detail: "暂时无法返回回执" }, 503);
      } else json(receipts.get(key));
    } else if (url.pathname.endsWith("/events") && req.method === "POST") {
      const key = req.headers["idempotency-key"];
      if (receipts.has(key)) { json(receipts.get(key), 202); return; }
      const event = body.events[0];
      if (event.type === "agent.session.input.message") {
        state.items.push(
          message("input-3", "user", event.input[0].content[0].text),
        );
        state.snapshot.yuxi.queued_input_count = 1;
      } else if (event.type === "agent.session.input.cancel") {
        state.snapshot.yuxi.current_turn.status = "cancelled";
        state.snapshot.yuxi.queue_paused = true;
        state.items.find((item) => item.role === "assistant").status =
          "incomplete";
        emit({ type: "agent.session.turn.cancelled" });
      } else if (event.type === "yuxi.session.input.continue") {
        state.snapshot.yuxi.queue_paused = false;
      } else if (event.type === "yuxi.session.input.resume") {
        state.snapshot.yuxi.current_turn.status = "in_progress";
        state.snapshot.yuxi.current_turn.waitpoint = null;
      }
      const accepted = { object: "yuxi.session.event.accepted", event_id: key, session_id: "thread-1", input_id: "input-3", turn_id: null, run_id: null, status: "accepted" };
      receipts.set(key, accepted);
      if (state.failNextEvent) { state.failNextEvent = false; json({ detail: "接收响应丢失" }, 503); }
      else json(accepted, 202);
    } else if (url.pathname.includes("/artifacts/")) {
      res.writeHead(200, {
        "Content-Type": "text/plain",
        "Content-Disposition":
          "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.txt",
      });
      res.end("真实下载字节\n");
    } else json({ detail: "未注册 fixture 路径" }, 404);
  });
  await new Promise((resolve) => server.listen(0, "0.0.0.0", resolve));

  function emit(event) {
    state.eventSeq += 1;
    const cursor = `v2-cursor-${state.eventSeq}`;
    const payload = {
      session_id: "thread-1",
      event_id: `event-${state.eventSeq}`,
      ...event,
    };
    for (const response of streams)
      response.write(
        `id: ${cursor}\nevent: ${event.type}\ndata: ${JSON.stringify(payload)}\n\n`,
      );
    return cursor;
  }

  return {
    requests,
    state,
    emit,
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    disconnect: () => {
      for (const stream of streams) stream.end();
    },
    close: async () => {
      for (const stream of streams) stream.end();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

let backend;
test.beforeEach(async ({ page }) => {
  backend = await fixture();
  await page.addInitScript((settings) => {
    if (!localStorage.getItem("yuxi-agent-demo:v1"))
      localStorage.setItem("yuxi-agent-demo:v1", JSON.stringify(settings));
  }, config(backend.baseUrl));
});
test.afterEach(async () => {
  await backend?.close();
});

test("桌面手机、平板和真实手机布局，配置持久化", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("选择 Agent")).toHaveValue("chat");
  const phone = await page.locator(".device-frame").boundingBox();
  expect(phone.width / phone.height).toBeCloseTo(9 / 16, 2);
  await page.getByRole("button", { name: "平板 4:3" }).click();
  const tablet = await page.locator(".device-frame").boundingBox();
  expect(tablet.width / tablet.height).toBeCloseTo(4 / 3, 2);
  await expect(page.getByLabel("对话列表", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "平板 4:3" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "关闭调试台" }).click();
  const mobile = await page.locator(".device-frame").boundingBox();
  expect(mobile.width).toBe(390);
  expect(mobile.height).toBe(844);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await expect(page.getByLabel("对话列表", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭对话列表" }).click();
  await page.getByRole("button", { name: "打开调试台" }).click();
  await expect(page.getByLabel("调试工作台", { exact: true })).toBeVisible();
});

test("历史回读、流式正文、生成中追加消息和携带身份下载文件", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("历史回答");
  await page.getByRole("button", { name: "新对话", exact: true }).click();
  await page.getByLabel("消息输入").fill("第一条消息");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("你好，");
  await expect(page.locator(".agent-status")).toContainText("正在回复");
  await page.getByLabel("消息输入").fill("追加消息");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("追加消息");
  const post = backend.requests.find(
    (request) =>
      request.body?.events?.[0]?.type === "agent.session.input.message",
  );
  expect(post.body.events[0].yuxi.mode).toBe("follow_up");
  expect(post.headers.authorization).toBe("Bearer test-alpha-key");
  expect(post.headers["idempotency-key"]).toBeTruthy();
  backend.state.items[1] = message("output-2", "assistant", "你好，已完成");
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    result_run_id: "run-1",
    status: "completed",
  };
  backend.state.artifacts = [
    "/home/user/workspace/报告 #1.txt",
    "/home/user/workspace/second.txt",
  ];
  backend.emit({
    type: "agent.session.turn.item.done",
    item: backend.state.items[1],
  });
  backend.emit({ type: "agent.session.turn.completed" });
  await expect(page.getByLabel("对话消息")).toContainText("你好，已完成");
  await expect(page.getByRole("button", { name: "second.txt 点击下载" })).toBeVisible();
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "报告 #1.txt 点击下载" }).click();
  const file = await downloading;
  expect(file.suggestedFilename()).toBe("报告.txt");
  expect(await readFile(await file.path(), "utf8")).toBe("真实下载字节\n");
  const downloadRequest = backend.requests.find((request) =>
    request.path.includes("/artifacts/"),
  );
  expect(downloadRequest.path).toContain(
    "%E6%8A%A5%E5%91%8A%20%231.txt?download=true",
  );
  expect(downloadRequest.headers.authorization).toBe("Bearer test-alpha-key");
});

test("APP 与用户切换清空旧消息，所有调用使用新的身份", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("历史回答");
  await page.getByRole("tab", { name: "用户", exact: true }).click();
  await page.getByRole("button", { name: "小 小林 customer-2" }).click();
  await expect(page.getByLabel("对话消息")).not.toContainText("历史回答");
  await expect
    .poll(() =>
      backend.requests.some(
        (request) =>
          request.path === "/api/v1/agents/" &&
          request.headers["x-end-user-id"] === "customer-2",
      ),
    )
    .toBe(true);
  await page.getByRole("tab", { name: "连接", exact: true }).click();
  await page.getByRole("button", { name: "B Beta APP beta" }).click();
  await expect(page.getByLabel("选择 Agent")).toContainText("beta Agent");
  const last = backend.requests
    .filter((request) => request.path === "/api/v1/agents/")
    .at(-1);
  expect(last.headers.authorization).toBe("Bearer test-beta-key");
  expect(last.headers["x-end-user-id"]).toBeUndefined();
});

test("局域网 HTTP 手机可以启动、发送、取消和新增 APP", async ({ page }) => {
  const address = Object.values(networkInterfaces())
    .flat()
    .find((item) => item.family === "IPv4" && !item.internal)?.address;
  expect(address, "需要真实局域网 IPv4 以验证不安全 HTTP Origin").toBeTruthy();
  const settings = config(backend.baseUrl.replace("127.0.0.1", address));
  await page.addInitScript(
    (value) =>
      localStorage.setItem("yuxi-agent-demo:v1", JSON.stringify(value)),
    settings,
  );
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`http://${address}:5180`);
  expect(
    await page.evaluate(() => ({
      secure: isSecureContext,
      randomUUID: typeof crypto.randomUUID,
    })),
  ).toEqual({ secure: false, randomUUID: "undefined" });
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByLabel("消息输入").fill("局域网消息");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("局域网消息");
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect
    .poll(() =>
      backend.requests.some(
        (request) =>
          request.body?.events?.[0]?.type === "agent.session.input.cancel",
      ),
    )
    .toBe(true);
  const keys = backend.requests
    .filter((request) => request.method === "POST")
    .map((request) => request.headers["idempotency-key"]);
  expect(keys).toHaveLength(2);
  expect(new Set(keys).size).toBe(2);
  for (const key of keys)
    expect(key).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  await page.getByRole("button", { name: "打开调试台" }).click();
  await page.getByRole("button", { name: "新增", exact: true }).click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          JSON.parse(localStorage.getItem("yuxi-agent-demo:v1")).apps.length,
      ),
    )
    .toBe(3);
  expect(errors).toEqual([]);
});

test("非法用户 ID 不切换或保存，中文显示名称配 ASCII ID 可以连接", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByRole("tab", { name: "用户", exact: true }).click();
  await page.getByLabel("显示名称").fill("用户甲");
  await page.getByLabel("用户 ID", { exact: true }).fill("用户甲");
  await page.getByRole("button", { name: "新增并切换" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "用户 ID 仅支持可打印 ASCII",
  );
  const stored = await page.evaluate(
    () => JSON.parse(localStorage.getItem("yuxi-agent-demo:v1")).apps[0],
  );
  expect(stored.userId).toBe("");
  expect(stored.users).toHaveLength(2);
  await page.getByLabel("用户 ID", { exact: true }).fill("customer-3");
  await page.getByRole("button", { name: "新增并切换" }).click();
  await expect
    .poll(() =>
      backend.requests.some(
        (request) => request.headers["x-end-user-id"] === "customer-3",
      ),
    )
    .toBe(true);
  await expect(
    page.getByRole("button", { name: /用户甲 customer-3/ }),
  ).toBeVisible();
});

test("APP 切换期间迟到的旧目录响应不能覆盖新 APP", async ({ page }) => {
  backend.state.delayAlpha = true;
  await page.goto("/");
  await page.getByRole("button", { name: "B Beta APP beta" }).click();
  await expect(page.getByLabel("选择 Agent")).toContainText("beta Agent");
  await page.waitForTimeout(600);
  await expect(page.getByLabel("选择 Agent")).not.toContainText("alpha Agent");
});

test("已接收但回执失败的输入手动重试复用同一幂等键", async ({ page }) => {
  backend.state.failNext = true;
  await page.goto("/");
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByLabel("消息输入").fill("只能保存一次");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("消息输入")).toBeDisabled();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByLabel("对话消息")).toContainText("只能保存一次");
  const creates = backend.requests.filter(
    (request) =>
      request.method === "POST" && request.path === "/api/v1/agents/sessions",
  );
  expect(creates).toHaveLength(2);
  expect(creates[0].headers["idempotency-key"]).toBe(
    creates[1].headers["idempotency-key"],
  );
  expect(creates[0].body).toEqual(creates[1].body);
  expect(
    backend.state.items.filter((item) => item.role === "user"),
  ).toHaveLength(1);
});

test("等待回答阻止普通发送，恢复提交完整等待点身份", async ({ page }) => {
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-1",
      kind: "answer",
      questions: [{ question_id: "q-1", question: "你的目标是什么？" }],
    },
  };
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("消息输入")).toBeDisabled();
  await page.getByLabel("你的目标是什么？").fill("展示对话能力");
  await page.getByRole("button", { name: "提交并继续" }).click();
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  const resume = backend.requests.find(
    (request) =>
      request.body?.events?.[0]?.type === "yuxi.session.input.resume",
  );
  expect(resume.body.events[0]).toEqual({
    type: "yuxi.session.input.resume",
    turn_id: "turn-1",
    waitpoint_id: "wait-1",
    response: {
      type: "answer",
      answers: [{ question_id: "q-1", answer: "展示对话能力" }],
    },
  });
});

test("暂停队列时发送仍提交 follow_up，继续按钮保留", async ({ page }) => {
  backend.state.snapshot.yuxi.queue_paused = true;
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "cancelled",
    waitpoint: null,
  };
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByRole("button", { name: "继续队列" })).toBeVisible();
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByLabel("消息输入").fill("只跑这一条");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("只跑这一条");
  const post = backend.requests.find(
    (request) =>
      request.body?.events?.[0]?.type === "agent.session.input.message",
  );
  expect(post.body.events[0]).toEqual({
    type: "agent.session.input.message",
    input: [
      {
        role: "user",
        content: [{ type: "input_text", text: "只跑这一条" }],
      },
    ],
    yuxi: { mode: "follow_up", attachment_file_ids: [] },
  });
});

test("选择题提交 option value，没有 options 时仍提交文本", async ({
  page,
}) => {
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-1",
      kind: "answer",
      questions: [
        { question_id: "q-text", question: "你的目标是什么？" },
        {
          question_id: "q-choice",
          question: "选择交付格式",
          options: [
            {
              label: "Markdown",
              value: "markdown",
              description: "便于继续编辑",
            },
            { label: "PDF", value: "pdf" },
          ],
          multi_select: false,
        },
      ],
    },
  };
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("消息输入")).toBeDisabled();
  await expect(page.getByRole("radio", { name: "Markdown" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "PDF" })).toBeVisible();
  await page.getByLabel("你的目标是什么？").fill("展示对话能力");
  await page.getByRole("radio", { name: "Markdown" }).check();
  await page.getByRole("button", { name: "提交并继续" }).click();
  const resume = backend.requests.find(
    (request) =>
      request.body?.events?.[0]?.type === "yuxi.session.input.resume",
  );
  expect(resume.body.events[0].response).toEqual({
    type: "answer",
    answers: [
      { question_id: "q-text", answer: "展示对话能力" },
      { question_id: "q-choice", answer: "markdown" },
    ],
  });
});

test("选择题支持自行填写，多选保留选项且可禁用其他", async ({ page }) => {
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-other",
      kind: "answer",
      questions: [
        { question_id: "single", question: "交付格式", options: [{ label: "PDF", value: "pdf" }] },
        { question_id: "multi", question: "读者", multi_select: true, options: [{ label: "开发", value: "dev" }] },
        { question_id: "restricted", question: "权限", allow_other: false, options: [{ label: "只读", value: "read" }] },
      ],
    },
  };
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  const single = page.getByRole("group", { name: "交付格式" });
  const multi = page.getByRole("group", { name: "读者" });
  const restricted = page.getByRole("group", { name: "权限" });
  await single.getByRole("radio", { name: "其他" }).check();
  await single.getByLabel("自行填写").fill("HTML");
  await multi.getByRole("checkbox", { name: "开发" }).check();
  await multi.getByRole("checkbox", { name: "其他" }).check();
  await multi.getByLabel("自行填写").fill("设计师");
  await expect(restricted.getByText("其他")).toHaveCount(0);
  await restricted.getByRole("radio", { name: "只读" }).check();
  await page.getByRole("button", { name: "提交并继续" }).click();
  const resume = backend.requests.find(
    (request) => request.body?.events?.[0]?.type === "yuxi.session.input.resume",
  );
  expect(resume.body.events[0].response).toEqual({
    type: "answer",
    answers: [
      { question_id: "single", answer: { type: "other", text: "HTML", selected: [] } },
      { question_id: "multi", answer: { type: "other", text: "设计师", selected: ["dev"] } },
      { question_id: "restricted", answer: "read" },
    ],
  });
});

test("多模态消息按内容块顺序显示文字与图片", async ({ page }) => {
  backend.state.items = [{
    ...message("input-77", "user", "ignored"),
    content: [
      { type: "input_text", text: "图片之前" },
      { type: "input_image", image_url: "data:image/png;base64,iVBORw0KGgo=" },
      { type: "input_text", text: "图片之后" },
    ],
  }];
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  const bubble = page.locator('[data-item-id="input-77"] .bubble');
  await expect(bubble).toContainText("图片之后");
  const sequence = await bubble.evaluate((node) =>
    [...node.children].map((child) => child.tagName === "IMG" ? "图片" : child.textContent.trim()),
  );
  expect(sequence).toEqual(["图片之前", "图片", "图片之后"]);
});

test("取消固定 Turn / Run，暂停队列可显式继续", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByLabel("消息输入").fill("运行一轮");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(
    page.getByRole("button", { name: "停止", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.getByRole("button", { name: "继续队列" })).toBeVisible();
  const cancel = backend.requests.find(
    (request) =>
      request.body?.events?.[0]?.type === "agent.session.input.cancel",
  );
  expect(cancel.body.events[0].yuxi).toEqual({
    turn_id: "turn-1",
    expected_run_id: "run-1",
  });
  await page.getByRole("button", { name: "继续队列" }).click();
  await expect(page.getByLabel("消息输入")).toBeEnabled();
});

test("断线续订带 cursor，resync 回读持久结果并忽略迟到增量", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByLabel("消息输入").fill("断线恢复");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByLabel("对话消息")).toContainText("你好，");
  const cursor = backend.emit({
    type: "agent.session.turn.output_text.done",
    item_id: "output-2",
    content_index: 0,
    text: "完成的正文",
  });
  await expect(page.getByLabel("对话消息")).toContainText("完成的正文");
  backend.disconnect();
  backend.state.items[1] = message("output-2", "assistant", "完成的正文");
  backend.state.snapshot.yuxi.current_turn.status = "completed";
  await expect.poll(() => backend.state.subscriptionCount).toBe(2);
  const resumed = backend.requests
    .filter(
      (request) => request.method === "GET" && request.path.endsWith("/events"),
    )
    .at(-1);
  expect(resumed.headers["last-event-id"]).toBe(cursor);
  backend.emit({ type: "yuxi.session.resync" });
  backend.emit({
    type: "agent.session.turn.output_text.delta",
    item_id: "output-2",
    content_index: 0,
    delta: "不应追加",
  });
  await expect(page.getByLabel("对话消息")).toContainText("完成的正文");
  await expect(page.getByLabel("对话消息")).not.toContainText("不应追加");
});

test("中文 Markdown 产物链接只编码一次并下载正确文件", async ({ page }) => {
  backend.state.items[1] = message(
    "output-2",
    "assistant",
    "[下载报告](/home/user/workspace/报告%20%231.txt)",
  );
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载报告" }).click();
  const download = await downloading;
  expect(await readFile(await download.path(), "utf8")).toBe("真实下载字节\n");
  expect(
    backend.requests.find((request) => request.path.includes("/artifacts/"))
      .path,
  ).toContain("/%E6%8A%A5%E5%91%8A%20%231.txt?download=true");
});

test("窄屏抽屉限制键盘焦点，Escape 关闭并归还焦点", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "打开调试台" });
  await trigger.click();
  await expect(page.getByRole("button", { name: "关闭调试台" })).toBeFocused();
  for (let index = 0; index < 20; index += 1) {
    await page.keyboard.press("Tab");
    // 原生 dialog 允许焦点进入浏览器 chrome，但不能进入背景控件。
    expect(
      await page.evaluate(
        () =>
          document.activeElement === document.body ||
          !!document.activeElement.closest("dialog"),
      ),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(
    page.getByLabel("调试工作台", { exact: true }),
  ).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test("旧 history 迟到不能把新的等待状态回退为 running", async ({ page }) => {
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "in_progress",
  };
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  await page.getByRole("tab", { name: /^事件/ }).click();
  backend.state.oldHistoryDelay = true;
  const oldRequest = page.waitForRequest((request) =>
    new URL(request.url()).pathname.endsWith("/items"),
  );
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await oldRequest;
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-new",
      kind: "answer",
      questions: [{ question_id: "q-1", question: "新问题" }],
    },
  };
  backend.emit({ type: "yuxi.session.turn.waiting" });
  await expect(page.getByLabel("新问题")).toBeVisible();
  await page.getByLabel("新问题").fill("保留我的回答");
  await page.waitForTimeout(600);
  await expect(page.getByLabel("消息输入")).toBeDisabled();
  await expect(page.getByLabel("新问题")).toHaveValue("保留我的回答");
});

test("审批恢复保留所有 call ID 和用户选择", async ({ page }) => {
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "requires_action",
    waitpoint: {
      id: "wait-approval",
      kind: "approval",
      calls: [
        {
          call_id: "call-1",
          name: "write_file",
          args: { path: "report.txt" },
          allowed_decisions: ["approve", "reject"],
        },
        {
          call_id: "call-2",
          name: "send_message",
          args: {},
          allowed_decisions: ["reject"],
        },
      ],
    },
  };
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("消息输入")).toBeDisabled();
  await page.getByLabel("write_file").selectOption("approve");
  await page.getByLabel("send_message").selectOption("reject");
  await page.getByRole("button", { name: "提交并继续" }).click();
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  const event = backend.requests.find(
    (request) =>
      request.body?.events?.[0]?.type === "yuxi.session.input.resume",
  ).body.events[0];
  expect(event.response).toEqual({
    type: "approval",
    decisions: [
      { call_id: "call-1", decision: "approve" },
      { call_id: "call-2", decision: "reject" },
    ],
  });
});

test("失败原因来自当前 Run，公开调试记录隐藏 Key", async ({ page }) => {
  backend.state.snapshot.yuxi.current_turn = {
    id: "turn-1",
    current_run_id: "run-1",
    status: "failed",
  };
  backend.state.runs = [
    { id: "run-1", status: "failed", error_message: "Insufficient Balance" },
  ];
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.locator(".turn-notice")).toContainText(
    "Insufficient Balance",
  );
  await expect.poll(() => backend.state.subscriptionCount).toBe(1);
  backend.emit({
    type: "yuxi.session.turn.capability_limited",
    message: "测试脱敏 test-alpha-key",
  });
  await page.getByRole("tab", { name: /^事件/ }).click();
  const event = page
    .locator(".event-entry")
    .filter({ hasText: "yuxi.session.turn.capability_limited" });
  await event.locator("summary").click();
  await expect(event.locator("pre")).toContainText("[已隐藏]");
  await expect(event.locator("pre")).not.toContainText("test-alpha-key");
});

test("附件上传不创建会话，PNG 直接输入；接收响应丢失时原意图重试", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("选择 Agent")).toHaveValue("chat");
  await page.locator('input[type=file]').nth(0).setInputFiles({ name: "draft.txt", mimeType: "text/plain", buffer: Buffer.from("draft bytes") });
  await expect(page.getByLabel("移除附件 draft.txt")).toBeVisible();
  const upload = backend.requests.find((request) => request.path === "/api/v1/agents/files");
  expect(upload.headers["content-type"]).toMatch(/^multipart\/form-data; boundary=/);
  expect(upload.raw).toContain("draft bytes");
  expect(backend.requests.some((request) => request.method === "POST" && request.path === "/api/v1/agents/sessions")).toBe(false);
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/CxcAAAAASUVORK5CYII=", "base64");
  await page.locator('input[type=file]').nth(1).setInputFiles({ name: "pixel.png", mimeType: "image/png", buffer: png });
  await expect(page.getByLabel("移除图片 pixel.png")).toBeVisible();
  await page.getByLabel("消息输入").fill("请读取附件和图片");
  backend.state.failNext = true;
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "重试", exact: true })).toBeVisible();
  await expect(page.getByLabel("移除附件 draft.txt")).toBeVisible();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByLabel("移除附件 draft.txt")).toHaveCount(0);
  const posts = backend.requests.filter((request) => request.method === "POST" && request.path === "/api/v1/agents/sessions");
  expect(posts).toHaveLength(2);
  expect(posts[1].headers["idempotency-key"]).toBe(posts[0].headers["idempotency-key"]);
  expect(posts[1].body).toEqual(posts[0].body);
  expect(posts[0].body.yuxi.attachment_file_ids).toEqual(["1234567890abcdef1234567890abcdef"]);
  expect(posts[0].body.input[0].yuxi).toBeUndefined();
  expect(posts[0].body.input[0].content[1]).toEqual({ type: "input_image", image_url: `data:image/png;base64,${png.toString("base64")}` });
  await page.screenshot({ path: "/tmp/yuxi-draft-demo.png", fullPage: true });
});


test("已有会话接收响应丢失后查询回执，原消息只提交一次", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByLabel("消息输入")).toBeEnabled();
  backend.state.failNextEvent = true;
  await page.getByLabel("消息输入").fill("原消息待确认");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "重试", exact: true })).toBeVisible();
  const post = backend.requests.find(request => request.method === "POST" && request.path.endsWith("/events"));
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByRole("button", { name: "重试", exact: true })).toBeHidden();
  await expect(page.getByText("原消息待确认", { exact: true })).toBeVisible();
  expect(backend.requests.filter(request => request.method === "POST" && request.path.endsWith("/events"))).toHaveLength(1);
  expect(backend.requests.some(request => request.method === "GET" && new URL(request.path, "http://fixture").searchParams.get("idempotency_key") === post.headers["idempotency-key"])).toBe(true);
});

test("历史按页读取，更早消息加载后最新正文仍在", async ({ page }) => {
  backend.state.items = Array.from({ length: 150 }, (_, index) => message(`input-${index}`, "user", `分页消息 ${index}`));
  await page.goto("/");
  await page.getByRole("button", { name: "打开对话列表" }).click();
  await page.getByRole("button", { name: "历史对话" }).click();
  await expect(page.getByText("分页消息 149", { exact: true })).toBeVisible();
  await expect(page.getByText("分页消息 0", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "加载更早消息" }).click();
  await expect(page.getByText("分页消息 0", { exact: true })).toBeVisible();
  await expect(page.getByText("分页消息 149", { exact: true })).toBeVisible();
  expect(backend.requests.filter(request => request.path.includes("/items?")).map(request => new URL(request.path, "http://fixture").searchParams.get("after"))).toContain("input-50");
});
