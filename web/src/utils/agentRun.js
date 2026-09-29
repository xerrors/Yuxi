export const isSteerableMainChatRun = (run) =>
  run?.status === 'running' && run?.run_type === 'chat' &&
  ['chat', 'public_api'].includes(run?.source)
