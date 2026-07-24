import test from 'node:test'
import assert from 'node:assert/strict'

import { isRunInterruptedConflict } from '../../src/utils/toolApproval.js'

test('识别等待用户回答或审批的结构化冲突', () => {
  const error = {
    response: {
      status: 409,
      data: {
        detail: {
          code: 'run_interrupted',
          message: '线程正在等待用户回答或审批'
        }
      }
    }
  }

  assert.equal(isRunInterruptedConflict(error), true)
  assert.equal(isRunInterruptedConflict(new Error('线程正在等待用户回答或审批')), false)
})
