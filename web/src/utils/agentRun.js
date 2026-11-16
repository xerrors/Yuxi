export const isSteerableMainChatRun = (run) =>
  run?.status === 'running' && run?.run_type === 'chat' && run?.source === 'chat'
