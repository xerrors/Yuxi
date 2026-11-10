/** 公开问题以非空 options 表示选择题；省略或空列表仍是文本问题。 */
export function questionHasOptions(question) {
  return Array.isArray(question?.options) && question.options.length > 0;
}

/** 创建当前 Thread 的公开消息投影。 */
export function createItems() {
  return { items: {}, seen: new Set(), closed: new Set() };
}

/** 保留完成块与较新的增量，用持久快照恢复丢失的完成边界。 */
export function mergeSnapshot(state, items) {
  for (const incoming of items || []) {
    const current = state.items[incoming.id];
    if (
      current &&
      ["completed", "failed"].includes(current.status) &&
      current.status !== incoming.status
    )
      continue;
    if (
      current &&
      current.status !== "in_progress" &&
      incoming.status === "in_progress"
    )
      continue;
    if (current?.phase === "final_answer" && incoming.phase !== "final_answer")
      continue;
    if (
      current?.status === "in_progress" &&
      incoming.status === "in_progress"
    ) {
      for (const index of incoming.yuxi?.completed_content_indices || []) {
        current.content[index] = structuredClone(incoming.content[index]);
      }
      for (const index of incoming.yuxi?.completed_reasoning_indices || []) {
        current.yuxi.reasoning ||= {};
        current.yuxi.reasoning[index] = incoming.yuxi.reasoning[index];
      }
    } else {
      state.items[incoming.id] = structuredClone(incoming);
    }
    for (const index of incoming.yuxi?.completed_content_indices || [])
      state.closed.add(`${incoming.id}:${index}`);
    for (const index of incoming.yuxi?.completed_reasoning_indices || [])
      state.closed.add(`reasoning:${incoming.id}:${index}`);
  }
}

/** 按逻辑事件去重，终态 item 不接受迟到的正文增量。 */
export function applyEvent(state, event) {
  if (!event.event_id || state.seen.has(event.event_id)) return;
  state.seen.add(event.event_id);
  if (event.type === "agent.session.turn.item.added") {
    if (!state.items[event.item.id]) mergeSnapshot(state, [event.item]);
    return;
  }
  if (event.type === "agent.session.turn.item.done") {
    mergeSnapshot(state, [event.item]);
    return;
  }
  const item = state.items[event.item_id];
  if (!item || item.status !== "in_progress") return;
  const index = event.content_index;
  const key = `${event.item_id}:${index}`;
  if (event.type === "agent.session.turn.output_text.delta") {
    if (state.closed.has(key)) return;
    item.content[index] ||= { type: "output_text", text: "" };
    item.content[index].text += event.delta;
  } else if (
    [
      "agent.session.turn.output_text.done",
      "agent.session.turn.content_part.done",
    ].includes(event.type)
  ) {
    item.content[index] = structuredClone(
      event.part || { type: "output_text", text: event.text },
    );
    state.closed.add(key);
  } else if (event.type === "agent.session.turn.content_part.added") {
    item.content[index] ||= structuredClone(event.part);
  } else if (event.type === "yuxi.session.turn.reasoning.delta") {
    if (state.closed.has(`reasoning:${key}`)) return;
    item.yuxi.reasoning ||= {};
    item.yuxi.reasoning[index] =
      (item.yuxi.reasoning[index] || "") + event.delta;
  } else if (event.type === "yuxi.session.turn.reasoning.done") {
    item.yuxi.reasoning ||= {};
    item.yuxi.reasoning[index] = event.text;
    state.closed.add(`reasoning:${key}`);
  }
}

/** 从公开 item 的持久顺序派生列表。 */
export function orderedItems(state) {
  return Object.values(state.items).sort(
    (a, b) =>
      (a.yuxi?.message_id || 0) - (b.yuxi?.message_id || 0) ||
      (a.yuxi?.output_index || 0) - (b.yuxi?.output_index || 0),
  );
}

/** 解码任意分块的 UTF-8 SSE，回调完成后才推进 cursor。 */
export async function readEvents(response, onEvent) {
  if (
    !response.headers.get("Content-Type")?.includes("text/event-stream") ||
    !response.body
  ) {
    throw new Error("服务未返回有效的 SSE 事件流");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const lines = frame.split(/\r?\n/);
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        const cursor = lines
          .find((line) => line.startsWith("id:"))
          ?.slice(3)
          .trim();
        if (data) await onEvent(JSON.parse(data), cursor);
      }
      if (done) return;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
