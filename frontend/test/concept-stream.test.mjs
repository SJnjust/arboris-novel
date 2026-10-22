import test from 'node:test'
import assert from 'node:assert/strict'
import { runtime, settle } from './runtime.mjs'

const final = { ai_message: '回复😀', ui_control: { type: 'text_input', placeholder: '继续' }, conversation_state: { genre: '悬疑' }, is_complete: false, ready_for_blueprint: null }
const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const response = chunks => new Response(new ReadableStream({ start(controller) {
  for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? new TextEncoder().encode(chunk) : chunk)
  controller.close()
} }), { headers: { 'Content-Type': 'text/event-stream' } })

for (const width of [1, 2, 19, 9999]) test(`SSE parser handles byte/chunk boundaries and multiple events (width=${width})`, async () => {
  const wire = new TextEncoder().encode((frame('ping', {}) + frame('delta', { text: '回复' }) + frame('delta', { text: '😀' }) + frame('final', final)).replaceAll('\n', '\r\n'))
  const chunks = []
  for (let index = 0; index < wire.length; index += width) chunks.push(wire.slice(index, index + width))
  const env = await runtime(async () => response(chunks))
  const parts = []
  const result = await env.api.NovelAPI.converseConceptStream('project-1', { value: '输入' }, {}, { clientMsgId: 'stable', onDelta: text => parts.push(text) })
  assert.deepEqual(JSON.parse(JSON.stringify(result)), final)
  assert.deepEqual(parts, ['回复', '😀'])
  assert.equal(JSON.parse(env.calls[0].options.body).client_msg_id, 'stable')
  assert.equal(env.calls[0].options.headers.get('Accept'), 'text/event-stream')
  assert.equal(env.clock.timers.size, 0)
})

test('SSE interrupted/malformed/error responses fail without losing received deltas', async () => {
  for (const ending of ['', 'event: delta\ndata: broken\n\n', frame('error', { detail: '模拟失败 api_key=private-fixture', retryable: true })]) {
    const env = await runtime(async () => response([frame('delta', { text: '部分回复' }), ending]))
    const parts = []
    await assert.rejects(env.api.NovelAPI.converseConceptStream('project-1', {}, {}, { clientMsgId: 'stable', onDelta: text => parts.push(text) }), error => {
      assert.equal(error instanceof env.api.ConceptStreamError, true)
      assert.equal(error.message.includes('private-fixture'), false)
      return true
    })
    assert.deepEqual(parts, ['部分回复'])
  }
})

test('InspirationMode keeps project/messages on error and retries original client_msg_id', async () => {
  let output
  let attempts = 0
  const env = await runtime(async url => {
    if (!url.endsWith('/stream')) return Response.json({ id: 'project-1', title: '灵感', chapters: [], conversation_history: [] })
    if (++attempts === 1) return new Response(new ReadableStream({ start(controller) { output = controller } }))
    return response([frame('delta', { text: final.ai_message }), frame('final', final)])
  })
  const store = await env.novelStore()
  const component = await env.component('../src/views/InspirationMode.vue', ['startConversation', 'chatMessages', 'conversationStarted', 'retryTurn', 'currentTurn'], store)
  const state = component.state
  const pending = state.startConversation()
  await settle()
  output.enqueue(new TextEncoder().encode(frame('delta', { text: '先显示部分' })))
  await settle()
  assert.equal(state.chatMessages.value[0].content, '先显示部分')
  assert.equal(store.currentProject.id, 'project-1')
  output.enqueue(new TextEncoder().encode(frame('error', { detail: '暂时失败', retryable: true })))
  output.close()
  await pending
  assert.equal(state.conversationStarted.value, true)
  assert.equal(store.currentProject.id, 'project-1')
  assert.equal(state.chatMessages.value[0].content, '先显示部分')
  assert.equal(state.chatMessages.value[0].error, '暂时失败')
  const id = state.chatMessages.value[0].clientMsgId
  await state.retryTurn(state.chatMessages.value[0])
  assert.equal(state.chatMessages.value.length, 1)
  assert.equal(state.chatMessages.value[0].content, final.ai_message)
  assert.equal(state.chatMessages.value[0].error, undefined)
  assert.equal(state.currentTurn.value, 1)
  assert.deepEqual(env.calls.filter(call => call.url.endsWith('/stream')).map(call => JSON.parse(call.options.body).client_msg_id), [id, id])
  assert.deepEqual(JSON.parse(JSON.stringify(store.currentConversationState)), final.conversation_state)
  component.stop()
})

test('leaving InspirationMode aborts the active stream and keeps its project', async () => {
  let cancelled = false
  const env = await runtime(async url => url.endsWith('/stream')
    ? new Response(new ReadableStream({ cancel() { cancelled = true } }))
    : Response.json({ id: 'project-1', title: '灵感', chapters: [], conversation_history: [] }))
  const store = await env.novelStore()
  const component = await env.component('../src/views/InspirationMode.vue', ['startConversation'], store)
  const pending = component.state.startConversation()
  await settle()
  component.stop()
  await pending
  assert.equal(cancelled, true)
  assert.equal(env.calls.find(call => call.url.endsWith('/stream')).options.signal.aborted, true)
  assert.equal(store.currentProject.id, 'project-1')
})

test('InspirationMode restores an interrupted user turn with its stored message id', async () => {
  const project = { id: 'project-1', title: '灵感', chapters: [], conversation_history: [
    { role: 'user', content: JSON.stringify({ value: '保留的输入' }), metadata: { client_msg_id: 'stored-turn' } }
  ] }
  const env = await runtime(async url => url.endsWith('/stream')
    ? response([frame('delta', { text: final.ai_message }), frame('final', final)]) : Response.json(project))
  const store = await env.novelStore()
  const component = await env.component('../src/views/InspirationMode.vue', ['restoreConversation', 'chatMessages', 'retryTurn'], store)
  await component.state.restoreConversation('project-1')
  assert.equal(component.state.chatMessages.value[0].content, '保留的输入')
  const pending = component.state.chatMessages.value[1]
  assert.equal(pending.clientMsgId, 'stored-turn')
  assert.match(pending.error, /尚未完成/)
  await component.state.retryTurn(pending)
  assert.equal(JSON.parse(env.calls.find(call => call.url.endsWith('/stream')).options.body).client_msg_id, 'stored-turn')
  assert.equal(component.state.chatMessages.value.length, 2)
  assert.equal(pending.content, final.ai_message)
  component.stop()
})

test('BlueprintConfirmation waits for real completion and rejects repeated clicks after 180s', async () => {
  let finish
  const env = await runtime(() => new Promise(resolve => { finish = resolve }))
  const store = await env.novelStore()
  store.currentProject = { id: 'project-1', title: '灵感', chapters: [], conversation_history: [] }
  const alerts = []
  const component = await env.component('../src/components/BlueprintConfirmation.vue', ['generateBlueprint', 'isGenerating'], store, { aiMessage: '开始生成' }, { showError: (...args) => alerts.push(args) })
  const pending = component.state.generateBlueprint()
  await settle()
  env.clock.now = 181000
  await component.state.generateBlueprint()
  assert.equal(component.state.isGenerating.value, true)
  assert.equal(env.calls.length, 1)
  assert.equal(env.clock.timers.size, 0)
  assert.deepEqual(alerts, [])
  finish(Response.json({ blueprint: { title: '已完成' }, ai_message: '完成' }))
  await pending
  assert.equal(component.state.isGenerating.value, false)
  assert.equal(component.emitted[0][0], 'blueprintGenerated')
  component.stop()
})
