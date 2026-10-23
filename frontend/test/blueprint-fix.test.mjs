import test from 'node:test'
import assert from 'node:assert/strict'
import { runtime, settle, job } from './runtime.mjs'
import { renderTemplate } from './render.mjs'

const final = { ai_message: '完整回复', ui_control: { type: 'text_input' }, conversation_state: {}, is_complete: false }
const frame = (event, data) => new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)

test('blueprint failure includes its actual step and retains a resume button in the rendered template', async () => {
  const failed = { ...job('failed'), kind: 'blueprint_generate', stage: 'failed',
    error: { status_code: 504, stage: 'volume_1_batch_2_of_4', step: '第 1 卷第 2/4 批大纲', detail: 'AI 生成超过总时限，请稍后重试或缩小生成范围' } }
  const env = await runtime(async () => Response.json([]))
  const message = env.volumes.jobError(failed)
  const html = await renderTemplate('../src/components/BlueprintConfirmation.vue', {
    renderedAiMessage: '<p>我们已经收集了足够的信息</p>', isGenerating: false, failure: message, hasCheckpoint: true,
  })
  assert.match(html, /第 1 卷第 2\/4 批大纲/)
  assert.match(html, /总时限/)
  assert.match(html, /从这里继续/)
  assert.match(html, /data-testid="blueprint-explanation"/)
  assert.match(html, /data-testid="blueprint-failure"/)
  assert.ok(html.indexOf('blueprint-explanation') < html.indexOf('blueprint-failure'))
})

test('failed blueprint resume submits resume=true and consumes the new result normally', async () => {
  let posted
  const failed = { ...job('failed'), kind: 'blueprint_generate', target: 'blueprint', error: { status_code: 504, stage: 'stage_volume_plan', step: '分卷规划', detail: '超时' } }
  const saved = { getItem: key => key.includes('blueprint_generate') ? JSON.stringify({ ids: [failed.id] }) : null, setItem() {}, removeItem() {} }
  const view = { id: 'project-1', chapters: [], blueprint: { volumes: [{ volume_number: 1, start_chapter: 1, end_chapter: 20 }], chapter_outline: [] } }
  const env = await runtime(async (url, options) => {
    if (url.includes('?')) return Response.json([])
    if (options.method === 'POST') { posted = JSON.parse(options.body); return Response.json({ job_id: 'next' }, { status: 202 }) }
    if (url.endsWith('/api/jobs/next')) return Response.json({ ...job(), id: 'next', kind: 'blueprint_generate' })
    if (url.includes('/api/jobs/')) return Response.json(failed)
    return Response.json(view)
  }, saved)
  const store = await env.novelStore(); store.currentProject = view
  const component = await env.component('../src/components/BlueprintConfirmation.vue', ['failure', 'generateBlueprint'], store, { aiMessage: '已收集' })
  await settle()
  assert.match(component.state.failure.value, /分卷规划：超时/)
  await component.state.generateBlueprint()
  assert.equal(posted.resume, true)
  component.stop()
})

test('empty volumes have explicit guidance and generation actions in real detail and writing templates', async () => {
  const groups = [{ volume_number: 1, title: '开端', summary: '', start_chapter: 1, end_chapter: 20, outline_cursor: 0, planned: 20, written: 0, outlines: [], status: 'planned' }]
  const jobs = { jobs: { value: [] }, error: { value: null }, activeJobs: { value: [] } }
  const html = await renderTemplate('../src/components/VolumePlanning.vue', { blueprint: { total_chapters: 20 }, groups, editable: true, projectId: 'project-1',
    jobs, busy: false, legacy: false, drafts: { 1: groups[0] }, error: '', planLabels: {} })
  assert.match(html, /第 1 卷大纲尚未生成/)
  assert.match(html, /生成本卷大纲 \/ 续跑/)
  const sidebar = await renderTemplate('../src/components/writing-desk/WDSidebar.vue', {
    project: { blueprint: {} }, groups, sidebarOpen: true, totalChapters: 0, characterCount: 0, relationshipCount: 0, hasIncompleteChapters: false,
    selectedForDeletion: [], isGeneratingOutline: false,
  })
  assert.match(sidebar, /第 1 卷大纲尚未生成/)
  assert.match(sidebar, /生成本卷大纲 \/ 续跑/)
  const shell = await renderTemplate('../src/components/shared/NovelDetailShell.vue', {
    activeSection: 'overview', sectionData: { overview: { missing_outline_volumes: [{ volume_number: 1, title: '开端' }] } },
    overviewMeta: {}, formattedTitle: '小说', isAdmin: false, isSidebarOpen: false, sections: [], isSectionLoading: false,
    currentError: null, currentComponent: null, componentProps: {},
  })
  assert.match(shell, /第 1 卷大纲尚未生成/)
  assert.match(shell, /前往生成 \/ 续跑卷大纲/)
})

test('legacy malformed assistant and unfinished user JSON do not break restoration', async () => {
  for (const history of [[{ role: 'assistant', content: '旧的纯文本回复' }], [{ role: 'user', content: '不是 JSON 的用户记录', metadata: { client_msg_id: 'old' } }]]) {
    const view = { id: 'project-1', chapters: [], conversation_history: history }
    const env = await runtime(async () => Response.json(view))
    const store = await env.novelStore()
    const component = await env.component('../src/views/InspirationMode.vue', ['restoreConversation', 'startError', 'conversationStarted', 'currentUIControl'], store)
    await component.state.restoreConversation('project-1')
    assert.equal(component.state.startError.value, '')
    assert.equal(component.state.conversationStarted.value, true)
    component.stop()
  }
})

test('single string options are one button and numeric options safely fall back to text input', async () => {
  for (const [options, count] of [['悬疑', 1], [7, 0]]) {
    const env = await runtime(async () => Response.json([]))
    const props = { loading: false, uiControl: { type: 'single_choice', options } }
    const component = await env.component('../src/components/ConversationInput.vue', ['choiceOptions', 'hasSingleChoices'], {}, props)
    assert.equal(component.state.choiceOptions.value.length, count)
    const html = await renderTemplate('../src/components/ConversationInput.vue', { ...props, hasSingleChoices: component.state.hasSingleChoices.value,
      choiceOptions: component.state.choiceOptions.value, isManualInput: false, textInput: '' })
    if (count) assert.match(html, />悬疑</)
    else assert.match(html, /<textarea/)
    component.stop()
  }
})

test('45 seconds without any SSE bytes cancels the body and makes the turn retryable', async () => {
  let cancelled = false
  const env = await runtime(async () => new Response(new ReadableStream({ cancel() { cancelled = true } })))
  const pending = env.api.NovelAPI.converseConceptStream('project-1', {}, {}, { clientMsgId: 'stable' })
  const rejected = assert.rejects(pending, error => error instanceof env.api.ConceptStreamError && error.message === '连接中断，请重试本轮' && error.retryable)
  await settle()
  assert.equal(env.clock.nextDelay(), 45000)
  await env.clock.next()
  await rejected
  assert.equal(cancelled, true)
  assert.equal(env.clock.timers.size, 0)
})

test('SSE ping bytes reset the inactivity timer without imposing a 45s overall deadline', async () => {
  let output
  const env = await runtime(async () => new Response(new ReadableStream({ start(controller) { output = controller } })))
  const pending = env.api.NovelAPI.converseConceptStream('project-1', {}, {}, { clientMsgId: 'stable' })
  await settle()
  env.clock.now = 44000
  output.enqueue(frame('ping', {}))
  await settle()
  assert.equal(env.clock.nextDelay(), 45000)
  env.clock.now = 60000
  output.enqueue(frame('final', final)); output.close()
  assert.equal((await pending).ai_message, '完整回复')
  assert.equal(env.clock.timers.size, 0)
})

test('stalled inspiration stream stops loading and keeps retry on its existing message', async () => {
  const env = await runtime(async url => url.endsWith('/stream') ? new Response(new ReadableStream()) : Response.json({ id: 'project-1', chapters: [], conversation_history: [] }))
  const store = await env.novelStore()
  const component = await env.component('../src/views/InspirationMode.vue', ['startConversation', 'chatMessages'], store)
  const pending = component.state.startConversation()
  await settle()
  await env.clock.next()
  await pending
  assert.equal(store.isLoading, false)
  assert.equal(component.state.chatMessages.value[0].error, '连接中断，请重试本轮')
  assert.equal(component.state.chatMessages.value[0].retryable, true)
  component.stop()
})

test('LLM settings form preserves and submits individual reasoning and structured overrides', async () => {
  let posted
  const env = await runtime(async (_url, options) => {
    if (options.method === 'PUT') { posted = JSON.parse(options.body); return Response.json(posted) }
    return Response.json({ llm_provider_model: 'gpt-6.1', reasoning_effort: 'xhigh', structured_model: 'structured-test', structured_reasoning_effort: 'low' })
  })
  const component = await env.component('../src/components/LLMSettings.vue', ['config', 'handleSave', 'efforts'], {}, {}, { showSuccess() {}, showError() {} })
  component.mount(); await settle()
  assert.equal(component.state.config.value.reasoning_effort, 'xhigh')
  await component.state.handleSave()
  assert.equal(posted.structured_reasoning_effort, 'low')
  assert.equal(posted.structured_model, 'structured-test')
  const html = await renderTemplate('../src/components/LLMSettings.vue', { config: component.state.config.value, efforts: component.state.efforts,
    showApiKey: false, availableModels: [], showModelDropdown: false, filteredModels: [], isLoadingModels: false })
  assert.match(html, /结构化生成模型/)
  assert.match(html, /xhigh 可能长时间推理/)
  component.stop()
})

test('SSE connection that never returns HTTP headers aborts after 45 seconds and is retryable', async () => {
  let aborted = false
  const env = await runtime((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
    aborted = true; reject(options.signal.reason)
  })))
  const pending = env.api.NovelAPI.converseConceptStream('project-1', {}, {}, { clientMsgId: 'stable' })
  const rejected = assert.rejects(pending, error => error instanceof env.api.ConceptStreamError && error.retryable && error.message === '连接中断，请重试本轮')
  await settle()
  assert.equal(env.clock.nextDelay(), 45000)
  await env.clock.next()
  await rejected
  assert.equal(aborted, true)
  assert.equal(env.clock.timers.size, 0)
})

test('planning completion refreshes both outline data and the cached overview missing-outline banner', async () => {
  let generated = false
  const env = await runtime(async url => Response.json({ data: url.endsWith('/overview')
    ? { title: '小说', missing_outline_volumes: generated ? [] : [{ volume_number: 1, title: '卷一' }] }
    : { chapter_outline: generated ? [{ chapter_number: 1, title: '章一', summary: '摘要' }] : [] } }))
  const component = await env.component('../src/components/shared/NovelDetailShell.vue', ['loadSection', 'handlePlanningUpdated', 'sectionData'], {}, { isAdmin: false })
  await component.state.loadSection('overview')
  assert.equal(component.state.sectionData.overview.missing_outline_volumes.length, 1)
  generated = true
  await component.state.handlePlanningUpdated()
  assert.equal(component.state.sectionData.overview.missing_outline_volumes.length, 0)
  assert.equal(component.state.sectionData.chapter_outline.chapter_outline.length, 1)
  component.stop()
})
