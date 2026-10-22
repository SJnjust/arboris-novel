import test from 'node:test'
import assert from 'node:assert/strict'
import { runtime, settle, job } from './runtime.mjs'

const volume = (number, start, end) => ({ volume_number: number, title: `卷${number}`, summary: '主线与冲突', start_chapter: start, end_chapter: end, outline_cursor: start - 1, status: 'planned' })
const project = () => ({ id: 'project-1', title: '已规划小说', chapters: [], conversation_history: [], blueprint: {
  title: '已规划小说', total_chapters: 40, volumes: [volume(1, 1, 20), volume(2, 21, 40)], chapter_outline: [{ chapter_number: 1, title: '开端', summary: '摘要', volume_number: 1 }]
} })

test('legacy and planned volume groups retain every outline and use maximum chapter number', async () => {
  const env = await runtime(async () => Response.json({}))
  const outlines = [{ chapter_number: 1, title: '一', summary: '' }, { chapter_number: 3, title: '三', summary: '' }]
  const legacy = env.volumes.volumeGroups({ chapter_outline: outlines })
  assert.equal(legacy.length, 1)
  assert.equal(legacy[0].is_virtual, true)
  assert.equal(legacy[0].end_chapter, 3)
  assert.equal(env.volumes.nextOutlineNumber(outlines), 4)
  assert.equal(env.volumes.nextOutlineNumber([]), 1)
  const blueprint = project().blueprint
  blueprint.chapter_outline.push({ chapter_number: 25, title: '二卷', summary: '', volume_number: 2 }, { chapter_number: 99, title: '未分配', summary: '' })
  const groups = env.volumes.volumeGroups(blueprint, [
    { chapter_number: 1, content: '已写', generation_status: 'successful' },
    { chapter_number: 25, versions: ['候选正文'], generation_status: 'waiting_for_confirm' }
  ])
  assert.deepEqual(Array.from(groups, row => row.outlines.length), [1, 1, 1])
  assert.deepEqual(Array.from(groups, row => row.written), [1, 1, 0])
  assert.equal(groups[0].planned, 20)
  assert.equal(groups[2].title, '未分卷章节')
})

test('blueprint jobs show real phases, consume completed ids before switching UI, then regenerate', async () => {
  const jobs = new Map()
  const view = project()
  let number = 0
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/jobs?')) return Response.json([])
    if (url.endsWith('/blueprint/generate')) {
      number++
      const current = { ...job('running', `bp-${number}`), kind: 'blueprint_generate', target: 'blueprint', payload: JSON.parse(options.body), stage: 'stage_core', progress: 5 }
      jobs.set(current.id, current)
      return Response.json({ job_id: current.id }, { status: 202 })
    }
    if (url.endsWith('/cancel')) {
      const current = jobs.get(url.split('/').at(-2))
      current.status = 'cancelled'; current.stage = 'cancelled'
      return Response.json(current)
    }
    if (url.includes('/api/jobs/')) return Response.json(jobs.get(url.split('/').at(-1)))
    return Response.json(view)
  })
  const store = await env.novelStore()
  store.currentProject = view
  const component = await env.component('../src/components/BlueprintConfirmation.vue', ['generateBlueprint', 'isGenerating', 'loadingText', 'progress', 'cancel'], store, { aiMessage: '生成' })
  await settle()
  await component.state.generateBlueprint()
  await component.state.generateBlueprint()
  assert.equal(number, 1)
  assert.equal(component.state.isGenerating.value, true)
  assert.equal(component.state.loadingText.value, '核心设定')
  env.clock.now = 181000
  assert.equal(component.state.isGenerating.value, true)
  const current = jobs.get('bp-1')
  current.stage = 'stage_volume_plan'; current.progress = 25
  await env.clock.next()
  assert.equal(component.state.loadingText.value, '分卷规划')
  current.stage = 'volume_1_batch_2_of_2'; current.progress = 70
  await env.clock.next()
  assert.equal(component.state.loadingText.value, '第 1 卷大纲 · 2/2 批')
  assert.equal(component.state.progress.value, 70)
  current.status = 'succeeded'; current.stage = 'succeeded'; current.progress = 100
  await env.clock.next()
  assert.equal(component.emitted[0][0], 'blueprintGenerated')
  const key = 'arboris:jobs:project-1:blueprint_generate'
  assert.deepEqual(JSON.parse(env.storage.getItem(key)).ids, [])
  assert.equal(component.state.isGenerating.value, false)
  component.stop()
  const fresh = await env.component('../src/components/BlueprintConfirmation.vue', ['generateBlueprint', 'cancel', 'isGenerating'], store, { aiMessage: '重新规划', regenerate: true })
  await settle()
  assert.equal(fresh.emitted.length, 0)
  await fresh.state.generateBlueprint()
  assert.equal(number, 2)
  assert.equal(jobs.get('bp-2').payload.resume, false)
  await fresh.state.cancel()
  assert.equal(fresh.state.isGenerating.value, false)
  assert.equal(jobs.get('bp-2').status, 'cancelled')
  await fresh.state.generateBlueprint()
  assert.equal(jobs.get('bp-3').payload.resume, true)
  assert.equal(number, 3)
  fresh.stop()
})

test('blueprint confirmation recovers active task on refresh without submitting another', async () => {
  const running = { ...job('running', 'restored'), kind: 'blueprint_generate', target: 'blueprint', payload: { resume: true }, stage: 'volume_1_batch_1_of_2', progress: 40 }
  const view = project()
  const env = await runtime(async url => Response.json(url.includes('/api/jobs?') ? [running] : url.includes('/api/jobs/') ? running : view))
  const store = await env.novelStore()
  store.currentProject = view
  const component = await env.component('../src/components/BlueprintConfirmation.vue', ['isGenerating', 'loadingText'], store, { aiMessage: '恢复' })
  await settle()
  assert.equal(component.state.isGenerating.value, true)
  assert.equal(component.state.loadingText.value, '第 1 卷大纲 · 1/2 批')
  assert.equal(env.calls.some(call => call.options.method === 'POST'), false)
  running.status = 'succeeded'; running.progress = 100
  await env.clock.next()
  assert.equal(component.emitted[0][0], 'blueprintGenerated')
  component.stop()
})

test('volume planning editor submits range/name changes and keeps server error readable', async () => {
  const view = project()
  let reject = true
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/jobs?')) return Response.json([])
    if (options.method === 'PATCH') {
      assert.equal(url.endsWith('/volumes/1'), true)
      const body = JSON.parse(options.body)
      assert.equal(body.title, '新卷名')
      assert.equal(body.start_chapter, 1)
      if (reject) return Response.json({ detail: '已有正文章节不能移出原卷' }, { status: 409 })
      view.blueprint.volumes[0] = { ...view.blueprint.volumes[0], ...body }
      return Response.json(view)
    }
    throw new Error(`Unexpected request ${url}`)
  })
  const component = await env.component('../src/components/VolumePlanning.vue', ['drafts', 'save', 'error'], {}, { blueprint: view.blueprint, projectId: view.id, editable: true })
  await settle()
  component.state.drafts.value[1].title = '新卷名'
  await component.state.save(1)
  assert.match(component.state.error.value, /不能移出原卷/)
  assert.equal(component.emitted.length, 0)
  reject = false
  await component.state.save(1)
  assert.equal(component.emitted[0][0], 'updated')
  assert.equal(component.emitted[0][1].blueprint.volumes[0].title, '新卷名')
  component.stop()
})

test('volume task API wrappers use 202 submission and stable idempotency keys', async () => {
  const env = await runtime(async () => Response.json({ job_id: 'volume-job' }, { status: 202 }))
  for (const call of [
    () => env.api.NovelAPI.generateVolumeOutline('project-1', 2, 'one-key'),
    () => env.api.NovelAPI.generateRemainingVolumes('project-1', 'all-key'),
    () => env.api.NovelAPI.smartSplitVolumes('project-1', 'split-key')
  ]) assert.equal((await call()).job_id, 'volume-job')
  assert.deepEqual(env.calls.map(call => call.url.split('/volumes/')[1]), ['2/outline', 'outline-all', 'smart-split'])
  assert.deepEqual(env.calls.map(call => call.options.headers.get('Idempotency-Key')), ['one-key', 'all-key', 'split-key'])
})
