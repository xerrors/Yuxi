import { test } from "node:test";
import assert from "node:assert/strict";
import { artifactPath, createApi, downloadFilename } from "../src/api.js";
import { loadConfig, normalizeBaseUrl, STORAGE_KEY } from "../src/config.js";

test("API、订阅和下载都使用固定 APP Key / end_user，幂等键与 cursor 保留", async () => {
  const captured = [];
  const config = {
    baseUrl: "http://localhost:5173/",
    key: "test-key",
    userId: "user-1",
    appId: "app-1",
  };
  const api = createApi(config, {
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response("{}", { headers: { "X-App-Id": "app-1" } });
    },
  });
  config.key = "another-key";
  config.userId = "another-user";
  await api.request("/sessions", {
    method: "POST",
    body: { input: [] },
    idempotencyKey: "request-1",
  });
  await api.events("thread-1", "v2-cursor");
  await api.download("thread-1", "/home/user/workspace/报告 #1.txt");
  assert.ok(
    captured.every(
      ({ options }) =>
        options.headers.Authorization === "Bearer test-key" &&
        options.headers["X-End-User-Id"] === "user-1",
    ),
  );
  assert.equal(captured[0].options.headers["Idempotency-Key"], "request-1");
  assert.equal(captured[1].options.headers["Last-Event-ID"], "v2-cursor");
  assert.match(
    captured[2].url,
    /%E6%8A%A5%E5%91%8A%20%231.txt\?download=true$/,
  );
  assert.ok(
    captured.every(
      ({ options }) =>
        options.credentials === "omit" && options.redirect === "error",
    ),
  );
});

test("托管浏览器的 HTTP 状态与设备回执不一致时拒绝展示", async () => {
  const config = { baseUrl: "http://localhost:5173", key: "test-key", userId: "", appId: "" };
  for (const [status, result] of [
    [202, { status: "ready", device_id: "device-1" }],
    [200, { status: "ready", device_id: "" }],
  ]) {
    const api = createApi(config, {
      fetcher: async () => new Response(JSON.stringify(result), { status }),
    });
    await assert.rejects(() => api.provisionManagedBrowser(), /无效的状态或设备 ID/);
  }
});

test("托管浏览器显示结构化错误的消息并保留 HTTP 状态", async () => {
  const api = createApi({ baseUrl: "http://localhost:5173", key: "test-key", userId: "", appId: "" }, {
    fetcher: async () => new Response(JSON.stringify({
      detail: { code: "abridge_managed_browser_paused", message: "aBridge 托管浏览器已暂停" },
    }), { status: 409 }),
  });
  await assert.rejects(() => api.provisionManagedBrowser(), { status: 409, message: "aBridge 托管浏览器已暂停" });
});

test("默认用户省略 X-End-User-Id，不发送客户端 X-App-Id", async () => {
  let headers;
  const api = createApi(
    {
      baseUrl: "http://localhost:5173",
      key: "test-key",
      userId: "",
      appId: "",
    },
    {
      fetcher: async (_url, options) => {
        headers = options.headers;
        return new Response("{}");
      },
    },
  );
  await api.listAgents();
  assert.equal(headers["X-End-User-Id"], undefined);
  assert.equal(headers["X-App-Id"], undefined);
});

test("APP ID 不匹配拒绝展示服务响应，HTTP 日志不包含 Key", async () => {
  const logs = [];
  const api = createApi(
    {
      baseUrl: "http://localhost:5173",
      key: "secret-test-value",
      userId: "",
      appId: "expected",
    },
    {
      log: (entry) => logs.push(entry),
      fetcher: async () =>
        new Response("{}", { headers: { "X-App-Id": "actual" } }),
    },
  );
  await assert.rejects(() => api.listAgents(), /APP ID 不一致/);
  assert.ok(!JSON.stringify(logs).includes("secret-test-value"));
});

test("无 Key 不发请求，网络错误与 422 校验错误显式展示", async () => {
  let requests = 0;
  const base = { baseUrl: "http://localhost:5173", userId: "", appId: "" };
  await assert.rejects(
    () =>
      createApi(
        { ...base, key: "" },
        {
          fetcher: async () => {
            requests += 1;
          },
        },
      ).listAgents(),
    /API Key/,
  );
  assert.equal(requests, 0);
  await assert.rejects(
    () =>
      createApi(
        { ...base, key: "key" },
        {
          fetcher: async () => {
            throw new TypeError("network");
          },
        },
      ).listAgents(),
    /跨域/,
  );
  await assert.rejects(
    () =>
      createApi(
        { ...base, key: "key" },
        {
          fetcher: async () =>
            new Response('{"detail":[{"msg":"格式不正确"}]}', { status: 422 }),
        },
      ).listAgents(),
    /格式不正确/,
  );
});

test("路径拒绝父级跳转，下载文件名支持 UTF-8", () => {
  assert.throws(() => artifactPath("thread", "/home/gem/../hidden"), /无效/);
  assert.throws(() => artifactPath("thread", ""), /无效/);
  assert.equal(
    downloadFilename(
      "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.txt",
      "fallback",
    ),
    "报告.txt",
  );
});

test("中文、换行和控制字符用户 ID 在 Header 边界明确拒绝且不发送请求", async () => {
  for (const userId of [
    "用户甲",
    "user\n1",
    "user\r1",
    "user\t1",
    "user\u007f1",
  ]) {
    let sent = false;
    const api = createApi(
      {
        baseUrl: "http://localhost:5173",
        key: "key",
        appId: "",
        userId,
      },
      {
        fetcher: async () => {
          sent = true;
          return new Response("{}");
        },
      },
    );
    await assert.rejects(() => api.listAgents(), /用户 ID.*ASCII/);
    assert.equal(sent, false);
  }
});

test("配置损坏显式报告，默认服务为 localhost:5173", () => {
  assert.equal(
    loadConfig({ getItem: () => null }).config.baseUrl,
    "http://localhost:5173",
  );
  const loaded = loadConfig({
    getItem: (key) => (key === STORAGE_KEY ? '{"apps":[]}' : null),
  });
  assert.match(loaded.error, /本地配置无法读取/);
  assert.equal(loaded.config.apps.length, 1);
  assert.throws(() => normalizeBaseUrl("javascript:alert(1)"), /HTTP/);
  assert.throws(
    () => normalizeBaseUrl("https://name:password@example.com"),
    /HTTP/,
  );
});
