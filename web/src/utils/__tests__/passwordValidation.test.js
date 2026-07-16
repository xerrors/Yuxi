import assert from 'node:assert/strict'

import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '../passwordValidation.js'

assert.equal(MIN_PASSWORD_LENGTH, 8)
assert.equal(isPasswordLongEnough('1234567'), false)
assert.equal(isPasswordLongEnough('12345678'), true)

console.log('passwordValidation: all assertions passed')
