import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('知识库详情将评估基准和近期评估收敛到同一个评估工作台', () => {
  const detailSource = readSource('../../src/pages/KnowledgeBaseInfoView.vue')
  const workspaceSource = readSource(
    '../../src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue'
  )
  const extensionsSource = readSource('../../src/pages/ExtensionsView.vue')
  const apiSource = readSource('../../src/apis/knowledge_api.js')

  assert.match(detailSource, /key: 'evaluation', label: '评估', icon: BarChart3/)
  assert.match(detailSource, /<KnowledgeEvaluationWorkspace/)
  assert.match(detailSource, /route\.query\.section/)
  assert.match(detailSource, /:can-manage="canManageKnowledgeBase"/)
  assert.match(extensionsSource, /<router-view v-else :key="route\.path" \/>/)
  assert.match(
    apiSource,
    /if \(params\.resultFilter !== undefined\) queryParams\.append\('result_filter', params\.resultFilter\)/
  )
  assert.doesNotMatch(apiSource, /error_only/)

  assert.match(workspaceSource, /id="evaluation-benchmarks-title">评估基准/)
  assert.match(workspaceSource, /id="recent-evaluations-title">近期评估/)
  assert.match(workspaceSource, />创建基准</)
  assert.match(workspaceSource, />新建评估</)
  assert.match(workspaceSource, /evaluationApi\.listDatasets\(props\.kbId\)/)
  assert.match(workspaceSource, /evaluationApi\.listRuns\(props\.kbId\)/)
})

test('评估基准和近期评估使用语义按钮导航到可深链接三级页面', () => {
  const workspaceSource = readSource(
    '../../src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue'
  )
  const routerSource = readSource('../../src/app/router/index.js')

  assert.match(routerSource, /path: 'knowledge-bases\/:kbId\/evaluation\/:datasetId'/)
  assert.match(routerSource, /name: 'ExtensionEvaluationBenchmarkDetail'/)
  assert.match(routerSource, /import\('\.\.\/\.\.\/pages\/EvaluationBenchmarkDetailView\.vue'\)/)
  assert.match(workspaceSource, /v-for="dataset in datasets"/)
  assert.match(workspaceSource, /class="evaluation-row-open"/)
  assert.match(workspaceSource, /const openDataset = \(dataset\) =>/)
  assert.match(workspaceSource, /name: 'ExtensionEvaluationBenchmarkDetail'/)
  assert.match(workspaceSource, /query: \{ view: 'results', run: run\.run_id \}/)
})

test('评估基准三级页包含题目和评估结果 Tab 并按当前基准过滤运行', () => {
  const source = readSource('../../src/pages/EvaluationBenchmarkDetailView.vue')
  const workspaceSource = readSource(
    '../../src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue'
  )

  assert.match(source, /<ExtensionDetailLayout/)
  assert.match(source, /\{ key: 'questions', label: '题目', icon: ClipboardList \}/)
  assert.match(source, /\{ key: 'results', label: '评估结果', icon: BarChart3 \}/)
  assert.match(source, /<template #panel-questions>/)
  assert.match(source, /<template #panel-results>/)
  assert.match(source, /placeholder="筛选当前页题目"/)
  assert.match(source, /resultFilterOptions/)
  assert.match(source, /const filter = resultFilter\.value/)
  assert.match(source, /resultFilter: filter/)
  assert.match(source, /label: getRunName\(run\)/)
  assert.match(source, /const getRunName = \(run\) => run\?\.name \|\| run\?\.run_name \|\| '未命名测试'/)
  assert.match(source, /仅查看错误/)
  assert.match(source, /错误及 R@10 < 1/)
  assert.match(source, /toFixed\(2\)/)
  assert.match(source, /key\.replace\('recall', 'R'\)/)
  assert.match(source, /key\.replace\('precision', 'P'\)/)
  assert.match(source, /筛选仅作用于当前页/)
  assert.match(source, /id="benchmark-run-select"/)
  assert.match(source, /v-model:value="selectedRunId"/)
  assert.match(source, /route\.query\.view === 'results'/)
  assert.match(source, /\(view\) => \{\s*activeTab\.value = view === 'results' \? 'results' : 'questions'/)
  assert.doesNotMatch(source, /\(\) => \[route\.params\.kbId, route\.params\.datasetId\]/)
  assert.match(
    source,
    /const getResultRowKey = \(record\) => `\$\{selectedRunId\.value\}:\$\{record\.item_index\}`/
  )
  assert.match(source, /response\.data\.filter\(\(run\) => run\.dataset_id === datasetId\.value\)/)
  assert.match(source, /evaluationApi\.getDataset\(kbId\.value, datasetId\.value, page, pageSize\)/)
  assert.match(source, /const runId = selectedRunId\.value/)
  assert.match(source, /evaluationApi\.getRunResults\(kbId\.value, runId/)
  assert.match(source, /query: \{ section: 'evaluation' \}/)
  assert.match(
    workspaceSource,
    /const formatMetric = \(value\) => \(Number\.isFinite\(value\) \? value\.toFixed\(2\) : '-'\)/
  )
})

test('评估运行状态只使用后端会写入的状态，未知状态两页文案一致', () => {
  const detail = readSource('../../src/pages/EvaluationBenchmarkDetailView.vue')
  const workspace = readSource('../../src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue')
  const statusText =
    "({ running: '运行中', completed: '已完成', failed: '失败' })[status] || '未知状态'"

  assert.match(detail, new RegExp(statusText.replace(/[()[\]{}.|+]/g, '\\$&')))
  assert.match(workspace, new RegExp(statusText.replace(/[()[\]{}.|+]/g, '\\$&')))
  assert.doesNotMatch(detail, /paused/)
  assert.doesNotMatch(workspace, /paused/)
})

test('评估详情源码包含所选运行的串行轮询与迟到响应守卫', () => {
  const source = readSource('../../src/pages/EvaluationBenchmarkDetailView.vue')

  assert.match(source, /selectedRun\.value\?\.status === 'running'/)
  assert.match(source, /activeTab\.value === 'results' && terminalRefreshPending/)
  assert.match(source, /if \(runsRefreshInFlight\) return/)
  assert.match(source, /runsRefreshInFlight = true/)
  assert.match(source, /window\.setTimeout\(\(\) => \{\s*refreshTimer = null\s*loadRuns\(true\)/)
  assert.match(source, /if \(disposed\) return/)
  assert.match(source, /const requestedRunId = silent \? selectedRunId\.value : String\(route\.query\.run \|\| ''\)/)
  assert.match(
    source,
    /else \{\s*selectedRunId\.value = ''\s*resultRequestId \+= 1\s*results\.value = \[\]\s*resultsLoading\.value = false/
  )
  assert.match(source, /if \(silent && resultsLoading\.value\) return/)
  assert.match(source, /if \(disposed \|\| requestId !== resultRequestId\) return/)
  assert.match(source, /terminalRefreshAfterRequestId = resultRequestId/)
  assert.match(source, /requestId > terminalRefreshAfterRequestId/)
  assert.match(source, /terminalRefreshPending = false\s*syncRefreshTimer\(\)/)
  assert.match(source, /results\.value\.length === 0 \|\| terminalRefreshPending/)
  assert.match(source, /await loadResults\(page, resultPagination\.pageSize, silent\)/)
  assert.match(
    source,
    /const handleRunSelection = async \(runId\) => \{[\s\S]*?resultRequestId \+= 1[\s\S]*?syncRefreshTimer\(\)[\s\S]*?Promise\.all/
  )
  assert.match(source, /onUnmounted\(\(\) => \{\s*disposed = true\s*resultRequestId \+= 1\s*stopRefreshTimer\(\)/)
})

test('新建评估在没有已完成基准时禁用且不伪造成功', () => {
  const workspaceSource = readSource(
    '../../src/modules/knowledge/ui/KnowledgeEvaluationWorkspace.vue'
  )
  const modalSource = readSource(
    '../../src/modules/knowledge/ui/EvaluationRunCreateModal.vue'
  )

  assert.match(workspaceSource, /:disabled="completedDatasets\.length === 0"/)
  assert.match(modalSource, /await evaluationApi\.runEvaluation\(props\.kbId/)
  assert.match(modalSource, /if \(response\?\.message !== 'success'\)/)
  assert.match(modalSource, /message\.error\(error\.message \|\| '启动评估失败'\)/)
  assert.match(modalSource, /hasAnswerModel !== hasJudgeModel/)
})
