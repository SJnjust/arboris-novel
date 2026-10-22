import test from 'node:test'
import assert from 'node:assert/strict'
import { File } from 'node:buffer'
import { runtime, settle, job } from './runtime.mjs'

const project = () => ({ id: 'project-1', title: '旧馆', chapters: [{ chapter_number: 1, title: '入口', content: '正文', versions: ['正文'], generation_status: 'successful' }], blueprint: { chapter_outline: [{ chapter_number: 1, title: '入口', summary: '调查' }] } })

test('writing desk submits evaluation and outline as tracked jobs, refreshes terminal data and can cancel/retry', async () => {
  let current, serial = 0, polls = 0
  const view = project()
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/jobs?')) return Response.json([])
    if (url.endsWith('/evaluate') || url.endsWith('/outline')) {
      current = { ...job('running', 'op-' + ++serial), kind: url.endsWith('/evaluate') ? 'chapter_evaluate' : 'outline_extend', payload: JSON.parse(options.body), target: url.endsWith('/evaluate') ? '1' : '2' }
      polls = 0
      return Response.json({ job_id: current.id }, { status: 202 })
    }
    if (url.endsWith('/cancel')) { current.status = 'cancelled'; return Response.json(current) }
    if (url.includes('/api/jobs/')) {
      if (current.status !== 'cancelled' && ++polls > 1) { current.status = 'succeeded'; current.progress = 100 }
      return Response.json(current)
    }
    if (url.endsWith('/api/novels/project-1')) {
      view.chapters[0].evaluation = '任务评审结果'
      return Response.json(view)
    }
    throw new Error('Unexpected URL: ' + url)
  })
  const store = await env.novelStore(); store.currentProject = view
  const alerts = { showError: message => { throw new Error(String(message)) }, showSuccess() {} }
  const component = await env.component('../src/views/WritingDesk.vue', ['selectedChapterNumber', 'evaluateChapter', 'handleGenerateOutline', 'operationJobs', 'cancelOperation', 'retryOperation'], store, { id: view.id }, alerts)
  await settle()
  component.state.selectedChapterNumber.value = 1
  await component.state.evaluateChapter()
  assert.equal(component.state.operationJobs.activeJobs.value[0].kind, 'chapter_evaluate')
  await env.clock.next()
  assert.equal(store.currentProject.chapters[0].evaluation, '任务评审结果')
  await component.state.handleGenerateOutline(2)
  const outline = component.state.operationJobs.activeJobs.value[0]
  assert.equal(outline.kind, 'outline_extend')
  assert.equal(outline.payload.start_chapter, 2)
  await component.state.cancelOperation(outline.id)
  assert.equal(component.state.operationJobs.activeJobs.value.length, 0)
  await component.state.retryOperation(outline)
  assert.equal(component.state.operationJobs.activeJobs.value[0].id, 'op-3')
  const keys = env.calls.filter(call => /\/(evaluate|outline)$/.test(call.url)).map(call => call.options.headers.get('Idempotency-Key'))
  assert.equal(new Set(keys).size, 3)
  component.stop()
})

test('optimizer keeps a running job visible and opens its persisted result after polling', async () => {
  let polls = 0
  const current = { ...job('running'), kind: 'chapter_optimize', payload: { chapter_number: 1, dimension: 'dialogue' } }
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/jobs?')) return Response.json([])
    if (url.endsWith('/optimizer/optimize')) {
      assert.equal(JSON.parse(options.body).dimension, 'dialogue')
      return Response.json({ job_id: current.id }, { status: 202 })
    }
    if (++polls > 1) { current.status = 'succeeded'; current.progress = 100; current.result = { optimized_content: '优化正文', optimization_notes: '调整节奏' } }
    return Response.json(current)
  })
  const component = await env.component('../src/components/writing-desk/workspace/ChapterContent.vue', ['selectedDimension', 'startOptimize', 'isOptimizing', 'showOptimizeResult', 'optimizedContent'], {}, { projectId: 'project-1', selectedChapter: project().chapters[0] }, { showError() {} })
  await settle()
  component.state.selectedDimension.value = 'dialogue'
  await component.state.startOptimize()
  assert.equal(component.state.isOptimizing.value, true)
  await env.clock.next()
  assert.equal(component.state.showOptimizeResult.value, true)
  assert.equal(component.state.optimizedContent.value, '优化正文')
  component.stop()
})

test('import attaches to the returned project job and navigates only after completion', async () => {
  let polls = 0, loaded = 0
  const current = { ...job('running'), kind: 'novel_import', target: 'project', payload: { filename: 'novel.txt' } }
  const env = await runtime(async (url, options) => {
    if (url.endsWith('/novels/import')) return Response.json({ job_id: current.id, project_id: current.project_id }, { status: 202 })
    if (url.includes('/api/jobs?')) return Response.json([current])
    if (url.includes('/api/jobs/')) {
      if (++polls > 1) { current.status = 'succeeded'; current.progress = 100; current.result = { id: 'project-1' } }
      return Response.json(current)
    }
    throw new Error('Unexpected URL: ' + url)
  })
  const component = await env.component('../src/views/NovelWorkspace.vue', ['handleFileImport', 'isImporting'], { loadProjects: async () => loaded++, projects: [] }, {}, { showError() {} })
  const target = { files: [new File(['第一章\n正文'], 'novel.txt')], value: 'novel.txt' }
  await component.state.handleFileImport({ target })
  await settle()
  assert.equal(component.state.isImporting.value, true)
  assert.equal(env.redirects.length, 0)
  await env.clock.next()
  assert.equal(env.redirects[0], '/novel/project-1')
  assert.equal(env.storage.getItem('arboris:import-project'), null)
  assert.ok(loaded >= 2)
  assert.equal(target.value, '')
  component.stop()
})

test('an import already finished before active-list recovery still consumes its returned job id', async () => {
  const done = { ...job('succeeded'), kind: 'novel_import', target: 'project', payload: { filename: 'novel.txt' }, result: { id: 'project-1' } }
  const env = await runtime(async url => {
    if (url.endsWith('/novels/import')) return Response.json({ job_id: done.id, project_id: done.project_id }, { status: 202 })
    return Response.json(url.includes('/api/jobs?') ? [] : done)
  })
  const component = await env.component('../src/views/NovelWorkspace.vue', ['handleFileImport'], { loadProjects: async () => {}, projects: [] }, {}, { showError() {} })
  await component.state.handleFileImport({ target: { files: [new File(['正文'], 'novel.txt')], value: 'novel.txt' } })
  await settle()
  assert.equal(env.redirects[0], '/novel/project-1')
  assert.equal(env.calls.some(call => call.url.endsWith('/api/jobs/job-1')), true)
  component.stop()
})
