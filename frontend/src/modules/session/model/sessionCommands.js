import { agentApi } from '@/apis'

/** 将回答或审批提交给用户操作的持久等待点。 */
export async function resumeSessionWaitpoint({
  threadId, turnId, runId, waitpointId, kind, actionRequests, answer, isCurrent
}) {
  if (!threadId || !turnId || !runId || !waitpointId) {
    throw new Error('当前线程没有等待中的 Turn')
  }
  const callIds = actionRequests?.map((call) => call.call_id) || []
  const turn = await agentApi.getThreadTurn(threadId, turnId)
  const waitpoint = turn.yuxi?.waitpoint
  if (
    !isCurrent() ||
    turn.status !== 'requires_action' ||
    turn.id !== turnId ||
    turn.yuxi?.current_run_id !== runId ||
    waitpoint?.id !== waitpointId ||
    waitpoint.run_id !== runId ||
    waitpoint.kind !== (kind === 'tool_approval' ? 'approval' : 'answer')
  ) {
    throw new Error('当前审批所属 Turn 已变化，请刷新后重试')
  }
  let response
  if (kind === 'tool_approval') {
    const calls = waitpoint.calls || []
    const decisions = new Map(callIds.map((id, index) => [id, answer?.decisions?.[index]?.type]))
    if (
      !calls.length ||
      calls.length !== decisions.size ||
      calls.some((call) => !['approve', 'reject'].includes(decisions.get(call.call_id)))
    ) {
      throw new Error('审批请求已变化，请刷新后重试')
    }
    response = {
      type: 'approval',
      decisions: calls.map((call) => ({
        call_id: call.call_id,
        decision: decisions.get(call.call_id)
      }))
    }
  } else {
    const questions = waitpoint.questions || []
    if (
      !questions.length ||
      questions.some((question) => !Object.hasOwn(answer || {}, question.question_id))
    ) {
      throw new Error('请回答全部问题后再提交')
    }
    response = {
      type: 'answer',
      answers: questions.map((question) => ({
        question_id: question.question_id,
        answer: answer[question.question_id]
      }))
    }
  }
  const accepted = await agentApi.resumeThreadTurn(threadId, {
    turn_id: turnId,
    waitpoint_id: waitpointId,
    response,
    idempotency_key: `resume-${waitpointId}`
  })
  if (!accepted?.run_id) {
    throw new Error('恢复已接收但未创建 Run，请刷新后查看 Turn 状态')
  }
  return accepted
}
