import type { Json, Remote, SseEvent } from "./types.js";
import { YuxiError } from "./types.js";
import { parseSse } from "./sse.js";

export class Client {
  readonly apiBase: string;
  constructor(
    private readonly remote: Remote,
    private readonly timeoutMs = 30_000,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {
    this.apiBase = `${remote.url.replace(/\/$/, "")}/api`;
  }

  async request<T extends Json | Json[] = Json>(path: string, init: RequestInit = {}, unauthenticated = false): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (!unauthenticated && this.remote.apiKey) headers.set("Authorization", `Bearer ${this.remote.apiKey}`);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(`${this.apiBase}${path}`, { ...init, headers, signal: controller.signal });
      if (!response.ok) throw await this.error(response);
      if (response.status === 204) return {} as T;
      const text = await response.text();
      if (!text) return {} as T;
      try { return JSON.parse(text) as T; } catch { throw new YuxiError("远程响应不是 JSON", response.status); }
    } catch (error) {
      if (error instanceof YuxiError) throw error;
      throw new YuxiError(`请求远程失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally { clearTimeout(timer); }
  }

  private async error(response: Response): Promise<YuxiError> {
    const text = await response.text();
    try {
      const detail = (JSON.parse(text) as Json).detail;
      if (typeof detail === "string") return new YuxiError(detail, response.status);
      if (Array.isArray(detail)) {
        const messages = detail.flatMap((item) => (item && typeof item === "object" && typeof (item as Json).msg === "string" ? [(item as Json).msg as string] : []));
        return new YuxiError(messages.join("；") || response.statusText, response.status);
      }
      if (detail && typeof detail === "object") {
        const codeValue = (detail as Json).error;
        const messageValue = (detail as Json).message;
        const code = typeof codeValue === "string" ? codeValue : "";
        const detailMessage = typeof messageValue === "string" ? messageValue : "";
        const message = detailMessage || code || response.statusText;
        return new YuxiError(code && message ? `${code}: ${message}` : message, response.status, code || undefined);
      }
    } catch { /* use status below */ }
    return new YuxiError(`${response.status} ${response.statusText}`, response.status);
  }

  health() { return this.request<Json>("/system/health", {}, true); }
  discovery() { return this.request<Json>("/system/discovery", {}, true); }
  me() { return this.request<Json>("/auth/me"); }
  createLoginSession() { return this.request<Json>("/auth/cli/sessions", { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } }, true); }
  exchangeLoginToken(deviceCode: string) { return this.request<Json>("/auth/cli/sessions/token", { method: "POST", body: JSON.stringify({ device_code: deviceCode }), headers: { "Content-Type": "application/json" } }, true); }
  deleteApiKey(id: string) { return this.request<Json>(`/user/apikey/${encodeURIComponent(id)}`, { method: "DELETE" }); }
  agents() { return this.request<Json>("/v1/agents"); }
  agent(id: string) { return this.request<Json>(`/v1/agents/${encodeURIComponent(id)}`); }
  threads(params = "") { return this.request<Json>(`/v1/agents/sessions${params}`); }
  async createThread(agentId: string, idempotencyKey: string) {
    const init = {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ agent_id: agentId }),
    };
    try { return await this.request<Json>("/v1/agents/sessions", init); }
    catch (error) {
      if (error instanceof YuxiError && error.status && error.status < 500 && error.status !== 429) throw error;
      return this.request<Json>("/v1/agents/sessions", init);
    }
  }
  async thread(id: string) {
    const session = await this.request<Json>(`/v1/agents/sessions/${encodeURIComponent(id)}`);
    return session;
  }
  turn(threadId: string, turnId: string) { return this.request<Json>(`/v1/agents/sessions/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}`); }
  items(id: string, params = "") { return this.request<Json>(`/v1/agents/sessions/${encodeURIComponent(id)}/items${params}`); }
  turns(id: string, params = "") { return this.request<Json>(`/v1/agents/sessions/${encodeURIComponent(id)}/turns${params}`); }
  input(id: string, inputId: string) { return this.request<Json>(`/v1/agents/sessions/${encodeURIComponent(id)}/inputs/${encodeURIComponent(inputId)}`); }
  receipt(id: string, key: string) { return this.request<Json>(`/v1/agents/sessions/${encodeURIComponent(id)}/receipt?${new URLSearchParams({ idempotency_key: key })}`); }
  /** 直接提交文件引用与完整图片 URL，不经过产品预处理 API。 */
  send(id: string, message: string, key: string, fileIds: string[] = [], imageUrls: string[] = []) {
    return this.event(id, { type: "agent.session.input.message", yuxi: { mode: "follow_up", attachment_file_ids: fileIds }, input: [{ role: "user", content: [{ type: "input_text", text: message }, ...imageUrls.map(image_url => ({ type: "input_image", image_url }))] }] }, key);
  }
  uploadFile(file: Blob, filename: string) {
    const body = new FormData();
    body.append("file", file, filename);
    return this.request<Json>("/v1/agents/files", { method: "POST", body });
  }
  file(id: string) { return this.request<Json>(`/v1/agents/files/${encodeURIComponent(id)}`); }
  deleteFile(id: string) { return this.request<Json>(`/v1/agents/files/${encodeURIComponent(id)}`, { method: "DELETE" }); }
  /** 未收到响应时查持久回执；未接收则只重放相同意图。 */
  async event(id: string, event: Json, key: string) {
    const path = `/v1/agents/sessions/${encodeURIComponent(id)}/events`;
    const init = { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ events: [event] }) };
    try { return await this.request<Json>(path, init); }
    catch (error) {
      if (error instanceof YuxiError && error.status && error.status < 500 && error.status !== 429) throw error;
      try { return await this.receipt(id, key); }
      catch (lookup) {
        if (!(lookup instanceof YuxiError) || lookup.status !== 404) throw lookup;
        return this.request<Json>(path, init);
      }
    }
  }
  async *events(id: string, cursor?: string): AsyncGenerator<SseEvent> {
    const headers = new Headers({ Accept: "text/event-stream" });
    if (this.remote.apiKey) headers.set("Authorization", `Bearer ${this.remote.apiKey}`);
    if (cursor) headers.set("Last-Event-ID", cursor);
    const controller = new AbortController();
    let response: Response;
    try { response = await this.fetchImpl(`${this.apiBase}/v1/agents/sessions/${encodeURIComponent(id)}/events`, { headers, signal: controller.signal }); }
    catch (error) { throw new YuxiError(`事件流连接失败: ${error instanceof Error ? error.message : String(error)}`); }
    try {
      if (!response.ok || !response.body) throw await this.error(response);
      yield* parseSse(response.body);
    } finally {
      controller.abort();
    }
  }
  kbList() { return this.request<Json[]>("/v1/knowledge/tools/list_kbs"); }
  kbFiles(id: string, query?: string) { return this.request<Json>("/v1/knowledge/tools/search_file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kb_id: id, query, offset: 0, limit: 100 }) }); }
  kbQuery(id: string, query: string) { return this.request<Json>("/v1/knowledge/tools/query_kb", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kb_id: id, query_text: query }) }); }
  kbOpen(kb: string, file: string) { return this.request<Json>("/v1/knowledge/tools/open_kb_document", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kb_id: kb, file_id: file, offset: 0, window_size: 200 }) }); }
  kbFind(kb: string, file: string, patterns: string[]) { return this.request<Json>("/v1/knowledge/tools/find_kb_document", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kb_id: kb, file_id: file, patterns, use_regex: false, case_sensitive: false, max_windows: 5, window_size: 80 }) }); }
}
