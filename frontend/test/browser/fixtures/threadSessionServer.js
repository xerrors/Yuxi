// 浏览器协议回放的独立 HTTP/SSE 服务；不替代真实 API/worker E2E。
import { createServer } from 'node:http'
import process from 'node:process'

const threads = new Map()
const streams = new Map()
const counts = { requests: [], opened: {}, closed: {} }
const delayed = new Map()
let eventId = 0
const item = (id, text, status = 'completed') => ({
  id: `${id}-message`, object: 'agent.session.item', type: 'message', role: 'assistant', status,
  phase: status === 'completed' ? 'final_answer' : undefined,
  turn_id: `turn-${id}`, content: [{ type: 'output_text', text }],
  yuxi: { message_id: `${id}-message`, run_id: `run-${id}`, created_at: '2026-11-17T01:00:00Z' }
})
const getThread = (id) => {
  if (!threads.has(id)) threads.set(id, { text: `${id} 历史`, status: 'in_progress' })
  return threads.get(id)
}
const turn = (id) => {
  const state = getThread(id)
  return { id: `turn-${id}`, status: state.status, yuxi: {
    current_run_id: `run-${id}`, result_run_id: state.status === 'completed' ? `run-${id}` : null,
    output: state.status === 'completed' ? [item(id, state.text)] : [],
    runs: [{ id: `run-${id}`, turn_id: `turn-${id}`, status: state.status === 'completed' ? 'completed' : 'running', artifacts: [] }]
  } }
}
/** 独立控制端修改协议事实并推送事件，浏览器 fixture 不接触客户端状态。 */
const control = (body) => {
  if (body.reset) {
    for (const connections of streams.values()) for (const response of connections) response.end()
    streams.clear(); threads.clear(); delayed.clear()
    counts.requests.length = 0; counts.opened = {}; counts.closed = {}
  }
  if (body.set) Object.assign(getThread(body.id), body.set)
  if (body.release) { delayed.get(body.id)?.(); delayed.delete(body.id) }
  if (body.event) {
    const event = { event_id: `event-${++eventId}`, session_id: body.id, turn_id: `turn-${body.id}`,
      yuxi: { run_id: `run-${body.id}` }, ...body.event }
    for (const response of streams.get(body.id) || []) response.write(`id: ${event.event_id}\ndata: ${JSON.stringify(event)}\n\n`)
  }
}
const server = createServer(async (request, response) => {
  response.setHeader('Access-Control-Allow-Origin', '*')
  response.setHeader('Access-Control-Allow-Headers', '*')
  if (request.method === 'OPTIONS') { response.writeHead(204).end(); return }
  const url = new URL(request.url, 'http://localhost')
  const json = (value, status = 200) => {
    response.writeHead(status, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify(value))
  }
  if (url.pathname === '/control') {
    let body = ''
    for await (const chunk of request) body += chunk
    if (body) control(JSON.parse(body))
    json({ ...counts, active: Object.fromEntries([...streams].map(([id, connections]) => [id, connections.size])) })
    return
  }
  counts.requests.push(url.pathname)
  if (/^\/api\/agent\/threads\/[^/]+\/feedback$/.test(url.pathname)) { json({ items: [] }); return }
  if (/^\/api\/agent\/threads\/[^/]+\/turns\/[^/]+\/references$/.test(url.pathname)) { json({ sources: [], references: null, available: false }); return }
  const match = url.pathname.match(/^\/api\/v1\/agents\/sessions\/([^/]+)(.*)$/)
  if (!match) { json({ detail: '此独立读取场景不允许依赖目录或 Agent 接口' }, 403); return }
  const [, id, suffix] = match
  const state = getThread(id)
  if (suffix === '/events') {
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    response.write(': connected\n\n')
    if (!streams.has(id)) streams.set(id, new Set())
    streams.get(id).add(response)
    counts.opened[id] = (counts.opened[id] || 0) + 1
    request.on('close', () => {
      streams.get(id).delete(response)
      counts.closed[id] = (counts.closed[id] || 0) + 1
    })
  } else if (suffix.endsWith('/items')) {
    const snapshot = { ...state }
    if (state.delay) await new Promise((resolve) => delayed.set(id, resolve))
    if (snapshot.fail) { state.fail = false; json({ detail: '历史暂不可用' }, 503); return }
    if (snapshot.rows && suffix === '/items') {
      const newest = Number(url.searchParams.get('after') || snapshot.rows + 1) - 1
      const limit = Number(url.searchParams.get('limit') || 100)
      const data = Array.from({ length: Math.min(limit, newest) }, (_, index) => {
        const number = newest - index
        return { id: String(number), object: 'agent.session.item', type: 'message', role: 'user', status: 'completed',
          turn_id: `history-turn-${number}`, content: [{ type: 'input_text', text: `历史段落 ${number}：` + '阅读位置应保留。'.repeat(20) }],
          yuxi: { message_id: String(number), run_id: `history-run-${number}`, created_at: new Date(1791580000000 + number * 1000).toISOString() } }
      })
      json({ data, last_id: data.at(-1)?.id, has_more: newest > limit,
        yuxi: { runs: data.map(row => ({ id: row.yuxi.run_id, turn_id: row.turn_id, status: 'completed', artifacts: [] })) } })
      return
    }
    json({ data: [item(id, snapshot.text, snapshot.status === 'completed' ? 'completed' : 'in_progress')],
      last_id: `${id}-message`, has_more: false, yuxi: { runs: turn(id).yuxi.runs } })
  } else if (suffix.startsWith('/turns/')) json(turn(id))
  else if (suffix === '/queue') json({ inputs: [], status: 'ready' })
  else if (suffix === '/state') json({ agent_state: {}, interrupt: null })
  else if (suffix === '/feedback') json({ items: [] })
  else if (!suffix) json({ id, object: 'agent.session', status: state.status, agent: { id: 'unavailable-agent' },
    yuxi: { title: id, current_turn: state.rows ? null : { id: `turn-${id}`, status: state.status } } })
  else json({ detail: '未声明协议' }, 404)
})
server.listen(5186, '127.0.0.1', () => console.log('Thread reading protocol server: 5186'))
process.on('SIGTERM', () => { for (const connections of streams.values()) for (const response of connections) response.end(); server.close() })
