import test from 'node:test'
import assert from 'node:assert/strict'
import { runtime, settle, storage, job } from './runtime.mjs'

const json = (value, status = 200) => Response.json(value, { status })
const key = 'arboris:jobs:project-1:chapter_generate'

test('request formats 422 arrays, preserves Headers/FormData and handles 401', async () => {
  let mode = 'validation'
  const env = await runtime(async (_url, options) => {
    if (mode === 'validation') return json({ detail: [{ loc: ['body', 'chapter_number'], msg: '需要整数' }, { loc: ['body', 'title'], msg: '不能为空' }] }, 422)
    if (mode === 'auth') return json({ detail: 'expired' }, 401)
    assert.equal(options.headers.get('X-Example'), 'yes')
    assert.equal(options.headers.has('Content-Type'), false)
    assert.equal('timeoutMs' in options, false)
    return json({ ok: true })
  })
  await assert.rejects(env.api.request('/api/test'), error => error.status === 422 && error.message === 'chapter_number：需要整数；title：不能为空')
  mode = 'upload'
  await env.api.request('/api/test', { method: 'POST', headers: new Headers({ 'X-Example': 'yes' }), body: new FormData() })
  mode = 'auth'
  await assert.rejects(env.api.request('/api/test'), /会话已过期/)
  assert.equal(env.auth.isAuthenticated, false)
  assert.deepEqual(env.redirects, ['/login'])
  assert.equal(env.clock.timers.size, 0)
})

test('request merges caller abort and timeout and cleans both timers', async () => {
  const env = await runtime((_url, options) => new Promise((_resolve, reject) => {
    if (options.signal.aborted) reject(options.signal.reason)
    else options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true })
  }))
  const deadline = env.api.request('/api/slow', { timeoutMs: 20 })
  const rejected = assert.rejects(deadline, /请求超时/)
  assert.equal(env.clock.nextDelay(), 20)
  await env.clock.next()
  await rejected
  const controller = new AbortController()
  const external = env.api.request('/api/slow', { signal: controller.signal })
  const aborted = assert.rejects(external, error => error.name === 'AbortError')
  controller.abort()
  await aborted
  assert.equal(env.clock.timers.size, 0)
})

test('useJob restores active jobs, backs off 1.5s to 8s and finishes once', async () => {
  const saved = storage()
  saved.setItem(key, JSON.stringify({ ids: ['job-1'] }))
  let status = 'running'
  let finished = 0
  const env = await runtime(async url => json(url.includes('?') ? [job(status)] : job(status)), saved)
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => 'project-1', { onTerminal: () => { finished++ } }))
  await settle()
  assert.equal(state.activeJobs.value.length, 1)
  for (const delay of [1500, 2250, 3375, 5062.5, 7593.75, 8000]) {
    assert.equal(env.clock.nextDelay(), delay)
    await env.clock.next()
  }
  status = 'succeeded'
  await env.clock.next()
  assert.equal(finished, 1)
  assert.equal(state.activeJobs.value.length, 0)
  assert.equal(env.clock.timers.size, 0)
  assert.deepEqual(JSON.parse(saved.getItem(key)).ids, [])
  scope.stop()
  assert.equal(env.document.listeners.size, 0)
})

test('useJob pauses when hidden, recovers on visibility and cancels server job', async () => {
  let status = 'running'
  const env = await runtime(async url => {
    if (url.endsWith('/cancel')) status = 'cancelled'
    return json(url.includes('?') ? [job(status)] : job(status))
  })
  const ended = []
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => 'project-1', { onTerminal: value => ended.push(value.status) }))
  await settle()
  env.document.hide(true)
  const calls = env.calls.length
  assert.equal(env.clock.timers.size, 0)
  await settle()
  assert.equal(env.calls.length, calls)
  env.document.hide(false)
  await settle()
  assert.ok(env.calls.length > calls)
  await state.cancel('job-1')
  assert.deepEqual(ended, ['cancelled'])
  assert.equal(state.activeJobs.value.length, 0)
  assert.equal(env.clock.timers.size, 0)
  scope.stop()
})

test('useJob retries the same submission key and attaches to a 409 job', async () => {
  const env = await runtime(async url => json(url.includes('?') ? [] : job()))
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => 'project-1'))
  await settle()
  const keys = []
  await assert.rejects(state.submit('1', async key => { keys.push(key); throw new Error('offline') }), /offline/)
  await state.submit('1', async key => { keys.push(key); return { job_id: 'job-1' } })
  assert.equal(keys[0], keys[1])
  assert.deepEqual(JSON.parse(env.storage.getItem(key)).ids, ['job-1'])
  await state.submit('1', async () => { throw new env.api.ApiError('already active', 409, { job_id: 'job-1' }) })
  assert.equal(state.jobs.value.length, 1)
  scope.stop()
})

test('useJob stops retrying failed polls and aborts in-flight work on disposal', async () => {
  let mode = 'fail'
  let aborted = false
  const env = await runtime(async (url, options) => {
    if (url.includes('?')) return json([job()])
    if (mode === 'fail') throw new Error('offline')
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => { aborted = true; reject(options.signal.reason) }))
  })
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => 'project-1'))
  await settle()
  for (let attempt = 0; attempt < 3; attempt++) await env.clock.next()
  assert.match(state.error.value, /offline/)
  assert.equal(env.clock.timers.size, 0)
  mode = 'wait'
  void state.recover()
  await settle()
  scope.stop()
  await settle()
  assert.equal(aborted, true)
  assert.equal(env.clock.timers.size, 0)
  assert.deepEqual(JSON.parse(env.storage.getItem(key)).ids, ['job-1'])
})

test('useJob ignores late responses after a project change', async () => {
  let finishOld
  const env = await runtime(async url => {
    const second = { ...job('running', 'job-2'), project_id: 'project-2' }
    if (url.includes('?')) return json(url.includes('project-2') ? [second] : [job()])
    if (url.endsWith('job-1')) return new Promise(resolve => { finishOld = () => resolve(json(job('succeeded'))) })
    return json(second)
  })
  const project = env.vue.ref('project-1')
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => project.value))
  await settle()
  project.value = 'project-2'
  await settle()
  finishOld()
  await settle()
  assert.equal(state.jobs.value.length, 1)
  assert.equal(state.jobs.value[0].project_id, 'project-2')
  scope.stop()
})

test('terminal refresh failures have bounded retries and keep the recoverable id', async () => {
  const saved = storage()
  saved.setItem(key, JSON.stringify({ ids: ['job-1'] }))
  const env = await runtime(async url => json(url.includes('?') ? [] : job('succeeded')), saved)
  let attempts = 0
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => 'project-1', { onTerminal() { attempts++; throw new Error('refresh unavailable') } }))
  await settle()
  for (let retry = 0; retry < 3; retry++) await env.clock.next()
  assert.equal(attempts, 4)
  assert.equal(env.clock.timers.size, 0)
  assert.match(state.error.value, /refresh unavailable/)
  assert.deepEqual(JSON.parse(saved.getItem(key)).ids, ['job-1'])
  scope.stop()
})

test('disposing a terminal job aborts its chained project refresh', async () => {
  const saved = storage()
  saved.setItem(key, JSON.stringify({ ids: ['job-1'] }))
  let aborted = false
  let refreshed = false
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/novels/')) return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => { aborted = true; reject(options.signal.reason) })
    })
    return json(url.includes('?') ? [] : job('succeeded'))
  }, saved)
  const scope = env.vue.effectScope()
  scope.run(() => env.useJob(() => 'project-1', { async onTerminal(value, signal) {
    await env.api.NovelAPI.getNovel(value.project_id, { signal })
    refreshed = true
  } }))
  await settle()
  scope.stop()
  await settle()
  assert.equal(aborted, true)
  assert.equal(refreshed, false)
  assert.equal(env.clock.timers.size, 0)
})

test('disposal aborts an in-flight cancel without reporting a false cancellation error', async () => {
  let aborted = false
  const env = await runtime(async (url, options) => {
    if (url.endsWith('/cancel')) return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => { aborted = true; reject(options.signal.reason) })
    })
    return json(url.includes('?') ? [job()] : job())
  })
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useJob(() => 'project-1'))
  await settle()
  const cancelling = state.cancel('job-1')
  await settle()
  scope.stop()
  await cancelling
  await settle()
  assert.equal(aborted, true)
  assert.equal(env.clock.timers.size, 0)
})

test('legacy synchronous APIs do not install a client deadline', async () => {
  const requests = []
  const env = await runtime((url, options) => new Promise(resolve => requests.push(() => resolve(json({ id: 'project-1' })))))
  const api = env.api.NovelAPI
  const calls = [
    () => api.importNovel(new Blob(['正文'])),
    () => api.converseConcept('project-1', { value: '测试' }),
    () => api.generateChapterOutline('project-1', 2, 1),
    () => api.evaluateChapter('project-1', 1),
    () => api.selectChapterVersion('project-1', 1, 0),
    () => env.api.OptimizerAPI.optimizeChapter({ project_id: 'project-1', chapter_number: 1, dimension: 'dialogue' })
  ]
  for (const call of calls) {
    const pending = call()
    await settle()
    assert.equal(env.clock.timers.size, 0)
    requests.shift()()
    await pending
  }
  const controller = new AbortController()
  let aborted = false
  const other = await runtime((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
    aborted = true; reject(options.signal.reason)
  })))
  const pending = other.api.request('/api/long', { timeoutMs: null, signal: controller.signal })
  const rejected = assert.rejects(pending, error => error.name === 'AbortError')
  assert.equal(other.clock.timers.size, 0)
  controller.abort()
  await rejected
  assert.equal(aborted, true)
})

test('failed version reasons are readable and redact credentials before display', async () => {
  const env = await runtime(async () => json({}))
  const detail = { message: '所有章节版本生成失败，请重试', failed_versions: [
    { index: 0, detail: '服务拒绝 api_key=private-fixture-key；请检查模型配置' },
    { index: 1, detail: '上游超时 Authorization: Bearer private-fixture-token；稍后重试 https://user:private-fixture-pass@fixture.invalid/path sk-fixture-secret' }
  ] }
  const message = env.api.formatApiDetail(detail)
  assert.match(message, /版本 1：服务拒绝/)
  assert.match(message, /版本 2：上游超时/)
  assert.match(message, /请检查模型配置/)
  for (const secret of ['private-fixture-key', 'private-fixture-token', 'private-fixture-pass', 'sk-fixture-secret']) assert.equal(message.includes(secret), false)
  assert.equal(env.api.formatApiDetail(message), message)
})

for (const existing of [true, false]) test(`WritingDesk restores pre-submit state on 429 (existing=${existing})`, async () => {
  const env = await runtime(async url => url.includes('/generate')
    ? json({ detail: '进行中的任务已达上限' }, 429) : json([]))
  const original = { chapter_number: 1, title: '旧馆', summary: '', content: '已生成正文', versions: ['已生成正文'], evaluation: null, generation_status: 'waiting_for_confirm' }
  const project = { id: 'project-1', title: '项目', blueprint: { chapter_outline: [{ chapter_number: 1, title: '旧馆', summary: '' }] }, chapters: existing ? [original] : [] }
  const store = env.vue.reactive({ currentProject: project,
    generateChapter: (number, key, options) => env.api.NovelAPI.generateChapter('project-1', number, key, options) })
  const alerts = []
  const desk = await env.writingDesk(store, { id: 'project-1' }, { showError: (...args) => alerts.push(args) })
  await settle()
  const before = JSON.parse(JSON.stringify(store.currentProject))
  await desk.generateChapter(1)
  assert.deepEqual(JSON.parse(JSON.stringify(store.currentProject)), before)
  assert.deepEqual(alerts, [['当前任务过多，请稍后再试', '生成受限']])
  desk.stop()
  await settle()
  assert.equal(env.clock.timers.size, 0)
})
