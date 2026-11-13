import { Tiktoken } from 'js-tiktoken/lite'
import cl100kBase from 'js-tiktoken/ranks/cl100k_base'

const encoding = new Tiktoken(cl100kBase)

/** 工具参数与结果各保留首 200、尾 100 token，短值保持原类型。 */
export function truncateMessageDebugToolValue(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  const tokens = encoding.encode(text, [], [])
  if (tokens.length <= 300) return value
  let head = tokens.slice(0, 200)
  let tail = tokens.slice(-100)
  // BPE token 可以落在 UTF-8 字符内部，向内收缩以免产生破损字符。
  while (encoding.decode(head).endsWith('\uFFFD')) head = head.slice(0, -1)
  while (encoding.decode(tail).startsWith('\uFFFD')) tail = tail.slice(1)
  return `${encoding.decode(head)}\n… [已省略中间内容] …\n${encoding.decode(tail)}`
}
