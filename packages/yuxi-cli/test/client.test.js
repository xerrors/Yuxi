import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { Client } from "../dist/client.js";

async function withServer(handler, run) {
  const server = createServer(handler).listen(0, "127.0.0.1");
  await once(server, "listening");
  try { return await run(`http://127.0.0.1:${server.address().port}`); }
  finally { server.close(); await once(server, "close"); }
}

function json(response, status, value) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(value));
}

test("uses Public v1 knowledge paths, bodies, and bearer authentication", async () => {
  const seen = [];
  await withServer((request, response) => {
    const record = { method: request.method, url: request.url, auth: request.headers.authorization };
    seen.push(record);
    let body = "";
    request.on("data", chunk => { body += chunk; });
    request.on("end", () => {
      record.body = body;
      if (request.url.endsWith("/list_kbs")) return json(response, 200, [{ kb_id: "kb 1", name: "Handbook" }]);
      if (request.url.endsWith("/search_file")) return json(response, 200, { files: [] });
      if (request.url.endsWith("/query_kb")) return json(response, 200, { kb_id: "kb 1", results: [] });
      if (request.url.endsWith("/open_kb_document")) return json(response, 200, { content: "ok" });
      if (request.url.endsWith("/find_kb_document")) return json(response, 200, { windows: [] });
      json(response, 404, { detail: "unexpected path" });
    });
  }, async url => {
    const client = new Client({ name: "test", url, apiKey: "yxkey_test" });
    assert.deepEqual(await client.kbList(), [{ kb_id: "kb 1", name: "Handbook" }]);
    await client.kbFiles("kb 1", "hand book");
    await client.kbQuery("kb 1", "what?");
    await client.kbOpen("kb 1", "file/1");
    await client.kbFind("kb 1", "file/1", ["needle"]);
  });
  assert.deepEqual(seen.map(item => [item.method, item.url]), [
    ["GET", "/api/v1/knowledge/tools/list_kbs"],
    ["POST", "/api/v1/knowledge/tools/search_file"],
    ["POST", "/api/v1/knowledge/tools/query_kb"],
    ["POST", "/api/v1/knowledge/tools/open_kb_document"],
    ["POST", "/api/v1/knowledge/tools/find_kb_document"],
  ]);
  assert.ok(seen.every(item => item.auth === "Bearer yxkey_test"));
  assert.deepEqual(JSON.parse(seen[1].body), { kb_id: "kb 1", query: "hand book", offset: 0, limit: 100 });
  assert.deepEqual(JSON.parse(seen[2].body), { kb_id: "kb 1", query_text: "what?" });
  assert.deepEqual(JSON.parse(seen[3].body), { kb_id: "kb 1", file_id: "file/1", offset: 0, window_size: 200 });
  assert.deepEqual(JSON.parse(seen[4].body), { kb_id: "kb 1", file_id: "file/1", patterns: ["needle"], use_regex: false, case_sensitive: false, max_windows: 5, window_size: 80 });
});

test("joins FastAPI validation messages when detail is an array", async () => {
  await withServer((_request, response) => json(response, 422, { detail: [{ loc: ["body", "name"], msg: "field required", type: "missing" }, { msg: "value error" }] }), async url => {
    const client = new Client({ name: "test", url, apiKey: "yxkey_test" });
    await assert.rejects(() => client.request("/error"), error => error.status === 422 && error.message === "field required；value error");
  });
});

test("maps structured HTTP errors without hiding the server status", async () => {
  await withServer((_request, response) => json(response, 422, { detail: { error: "invalid_input", message: "bad request" } }), async url => {
    const client = new Client({ name: "test", url, apiKey: "yxkey_test" });
    await assert.rejects(() => client.request("/error"), error => error.status === 422 && error.code === "invalid_input" && error.message === "invalid_input: bad request");
  });
});

test("preserves Session resources through CLI create and retrieve", async () => {
  const session = { id: "session/1", object: "agent.session", status: "idle", agent: { id: "agent", model: "model" }, yuxi: { title: "session", archived: false } };
  const seen = [];
  await withServer((request, response) => {
    seen.push({ url: request.url, key: request.headers["idempotency-key"] });
    json(response, request.method === "POST" ? 201 : 200, session);
  }, async url => {
    const client = new Client({ name: "test", url, apiKey: "yxkey_test" });
    assert.deepEqual(await client.createThread("agent", "create-key"), session);
    assert.deepEqual(await client.thread("session/1"), session);
  });
  assert.deepEqual(seen, [
    { url: "/api/v1/agents/sessions", key: "create-key" },
    { url: "/api/v1/agents/sessions/session%2F1", key: undefined },
  ]);
});

test("uses the server status when a structured error omits its code and message", async () => {
  await withServer((_request, response) => json(response, 503, { detail: {} }), async url => {
    const client = new Client({ name: "test", url });
    await assert.rejects(() => client.request("/error"), error => error.status === 503 && error.message === "Service Unavailable");
  });
});

test("uploads binary draft once and sends its id with the original PNG data URL", async () => {
  const requests = [];
  const file = { id: "1234567890abcdef1234567890abcdef", object: "file", filename: "source.txt", bytes: 8 };
  const client = new Client({ name: "test", url: "http://fixture", apiKey: "test-key" }, 30000, async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify(url.endsWith("/files") ? file : { input_id: "input" }), { status: 201 });
  });
  assert.deepEqual(await client.uploadFile(new Blob(["original"], { type: "text/plain" }), "source.txt"), file);
  assert.equal(requests[0].options.headers.get("Content-Type"), null);
  assert.equal(await requests[0].options.body.get("file").text(), "original");
  const png = "data:image/png;base64,iVBORw0KGgo=";
  await client.send("thread", "read", "send-key", [file.id], [png]);
  const event = JSON.parse(requests[1].options.body).events[0];
  assert.deepEqual(event.yuxi.attachment_file_ids, [file.id]);
  assert.equal("yuxi" in event.input[0], false);
  assert.deepEqual(event.input[0].content[1], { type: "input_image", image_url: png });
  assert.equal(requests[1].options.headers.get("Idempotency-Key"), "send-key");
  assert.equal(requests.length, 2);
});

test("已接收的 POST 响应被断开后用原键读取回执，不重新提交", async () => {
  const seen = [];
  const receipt = { object: "yuxi.session.event.accepted", session_id: "session", input_id: "fixed-input", turn_id: null, run_id: null, event_id: "receipt", status: "accepted" };
  await withServer((request, response) => {
    seen.push({ method: request.method, path: request.url, key: request.headers["idempotency-key"] });
    if (request.method === "POST") { request.resume(); request.on("end", () => response.destroy()); }
    else json(response, 200, receipt);
  }, async url => {
    const client = new Client({ name: "test", url });
    assert.deepEqual(await client.send("session", "original", "key/with spaces"), receipt);
  });
  assert.deepEqual(seen, [
    { method: "POST", path: "/api/v1/agents/sessions/session/events", key: "key/with spaces" },
    { method: "GET", path: "/api/v1/agents/sessions/session/receipt?idempotency_key=key%2Fwith+spaces", key: undefined },
  ]);
});
