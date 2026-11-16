export const createThreadForContext = async ({ context, getCurrentContext, requestId, create }) => {
  const thread = await create(requestId)
  const current = getCurrentContext()
  return {
    thread,
    accepted:
      Boolean(thread) &&
      context.agentId === current.agentId &&
      context.projectId === current.projectId &&
      context.threadId === current.threadId
  }
}
