import { existsSync, readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
import process from 'node:process'

const testDirectories = ['tests/unit', 'src/utils/__tests__']
const testFiles = testDirectories.flatMap((directory) => {
  if (!existsSync(directory)) return []
  return readdirSync(directory)
    .filter((filename) => filename.endsWith('.test.js') || filename.endsWith('.spec.js'))
    .map((filename) => join(directory, filename))
})

if (!testFiles.length) {
  throw new Error('未找到前端单元测试')
}

const result = spawnSync(process.execPath, ['--test', ...testFiles], {
  stdio: 'inherit'
})
process.exitCode = result.status ?? 1
