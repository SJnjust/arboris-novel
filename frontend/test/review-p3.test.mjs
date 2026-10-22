import test from 'node:test'
import assert from 'node:assert/strict'
import { runtime, settle, job } from './runtime.mjs'

const virtual = { volume_number: 1, title: '旧规划', summary: '旧梗概', start_chapter: 1, end_chapter: 40, status: 'outlined', outline_cursor: 40, is_virtual: true }
const project = { id: 'project-1', chapters: [], blueprint: { total_chapters: 40, chapter_outline: [{ chapter_number: 1, title: '入口', summary: '调查' }], volumes: [{ ...virtual, title: '新的第一卷', summary: '新规划', end_chapter: 20, is_virtual: false }, { ...virtual, volume_number: 2, title: '新的第二卷', start_chapter: 21, is_virtual: false }] } }

test('confirmed smart split discards dirty plan, blocks stale saves and PATCHes only the new server plan', async () => {
  let done = false, patch, confirmations = 0
  const running = { ...job('running'), kind: 'volume_smart_split', target: 'plan', payload: {} }
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/jobs?')) return Response.json([])
    if (url.endsWith('/smart-split')) return Response.json({ job_id: running.id }, { status: 202 })
    if (url.includes('/api/jobs/')) return Response.json(done ? { ...running, status: 'succeeded', progress: 100 } : running)
    if (options.method === 'PATCH') { patch = JSON.parse(options.body); return Response.json(project) }
    if (url.endsWith('/api/novels/project-1')) return Response.json(project)
    throw new Error('Unexpected request: ' + url)
  })
  const props = env.vue.reactive({ projectId: 'project-1', editable: true, blueprint: { total_chapters: 40, chapter_outline: [], volumes: [virtual] } })
  const component = await env.component('../src/components/VolumePlanning.vue', ['drafts', 'smartSplit', 'save', 'busy'], {}, props,
    { showConfirm: async () => { confirmations++; return true } })
  await settle()
  component.state.drafts.value[1].title = '未保存的旧草稿'
  component.state.drafts.value[1].end_chapter = 35
  await component.state.smartSplit()
  assert.equal(component.state.drafts.value[1].title, '旧规划')
  assert.equal(component.state.busy.value, true)
  await component.state.save(1)
  assert.equal(patch, undefined)
  done = true
  await env.clock.next()
  assert.equal(component.state.busy.value, false)
  assert.equal(component.state.drafts.value[1].title, '新的第一卷')
  assert.equal(component.state.drafts.value[1].end_chapter, 20)
  assert.equal(component.state.drafts.value[2].start_chapter, 21)
  props.blueprint = project.blueprint
  await settle()
  await component.state.save(1)
  assert.deepEqual(patch, { title: '新的第一卷', summary: '新规划', start_chapter: 1, end_chapter: 20 })
  assert.equal(confirmations, 1)
  component.stop()
})

test('body drafts are isolated by user, ignore legacy keys, and stop writing after account changes', async () => {
  const env = await runtime(async () => Response.json([]))
  let prompts = 0
  env.setAlerts({ showConfirm: async () => { prompts++; return true } })
  const scope = env.vue.effectScope()
  const draft = scope.run(() => env.drafts.useBodyDraft())
  env.storage.setItem('arboris:body-draft:project-1:1', JSON.stringify({ content: '没有归属的旧格式草稿' }))
  env.storage.setItem('arboris:body-draft:1:project-1:1', JSON.stringify({ content: '用户一草稿' }))
  await draft.open('project-1', 1, '服务端正文')
  assert.equal(draft.content.value, '用户一草稿')
  draft.content.value = '用户一新编辑'
  env.auth.user = { id: 2 }
  assert.equal(draft.active.value, false)
  assert.equal(draft.content.value, '')
  await draft.open('project-1', 1, '服务端正文')
  assert.equal(prompts, 1)
  assert.equal(draft.content.value, '服务端正文')
  draft.content.value = '用户二新编辑'
  assert.equal(JSON.parse(env.storage.getItem('arboris:body-draft:1:project-1:1')).content, '用户一新编辑')
  assert.equal(JSON.parse(env.storage.getItem('arboris:body-draft:2:project-1:1')).content, '用户二新编辑')
  env.auth.user = { id: 1 }
  await draft.open('project-1', 1, '服务端正文')
  assert.equal(draft.content.value, '用户一新编辑')
  assert.equal(prompts, 2)
  env.auth.user = null
  assert.equal(await draft.open('project-1', 1, '服务端正文'), false)
  assert.equal(JSON.parse(env.storage.getItem('arboris:body-draft:project-1:1')).content, '没有归属的旧格式草稿')
  scope.stop()
})

test('both chapter regeneration confirmations name all versions and reviews; cancellation emits nothing', async () => {
  const env = await runtime(async () => Response.json([]))
  const messages = []
  const alerts = { showConfirm: async message => { messages.push(message); return false } }
  const view = { id: 'project-1', chapters: [], blueprint: { chapter_outline: [] } }
  const workspace = await env.component('../src/components/writing-desk/WDWorkspace.vue', ['confirmRegenerateChapter'], {}, { project: view, selectedChapterNumber: 3 }, alerts)
  await workspace.state.confirmRegenerateChapter()
  assert.equal(workspace.emitted.length, 0)
  const sidebar = await env.component('../src/components/writing-desk/WDSidebar.vue', ['confirmGenerateChapter'], {}, { project: view }, alerts)
  await sidebar.state.confirmGenerateChapter(4)
  assert.equal(sidebar.emitted.length, 0)
  assert.match(messages[0], /第 3 章的全部正文版本和评审/)
  assert.match(messages[1], /第 4 章的全部正文版本和评审/)
  for (const message of messages) assert.match(message, /无法撤销/)
  workspace.stop(); sidebar.stop()
})

test('conversation input reports unsent text and clears it when the round is submitted', async () => {
  const env = await runtime(async () => Response.json([]))
  const component = await env.component('../src/components/ConversationInput.vue', ['textInput', 'handleTextSubmit', 'handleOptionSelect'], {}, { loading: false, uiControl: { type: 'text_input' } })
  component.state.textInput.value = '尚未发送的想法'
  assert.deepEqual(component.emitted.at(-1), ['draftChange', '尚未发送的想法'])
  component.state.handleTextSubmit()
  assert.equal(component.emitted.at(-2)[0], 'submit')
  assert.deepEqual(component.emitted.at(-1), ['draftChange', ''])
  component.state.textInput.value = '改为选择预设选项'
  component.state.handleOptionSelect('choice', '选项内容')
  assert.deepEqual(component.emitted.at(-1), ['draftChange', ''])
  component.stop()
})

test('inspiration route/unload guards protect unsent input', async () => {
  const env = await runtime(async () => Response.json([]))
  const component = await env.component('../src/views/InspirationMode.vue', ['unsentInput', 'conversationGuard'], {}, {}, { showConfirm: async () => false })
  component.state.unsentInput.value = '还没发送'
  assert.equal(await env.leaveGuards[0](), false)
  assert.equal(await env.updateGuards[0](), false)
  const event = { preventDefault() { this.prevented = true } }
  component.state.conversationGuard.beforeUnload(event)
  assert.equal(event.prevented, true)
  component.state.unsentInput.value = ''
  assert.equal(await env.leaveGuards[0](), true)
  component.stop()
})

test('cancelling navigation during an SSE reply leaves upstream running; confirmed leave aborts it', async () => {
  let cancelled = false, accept = false
  const env = await runtime(async url => url.endsWith('/stream')
    ? new Response(new ReadableStream({ cancel() { cancelled = true } }))
    : Response.json({ id: 'project-1', title: '灵感', chapters: [], conversation_history: [] }))
  const store = await env.novelStore()
  const component = await env.component('../src/views/InspirationMode.vue', ['startConversation', 'conversationGuard'], store, {}, { showConfirm: async () => accept })
  const pending = component.state.startConversation()
  await settle()
  assert.equal(await env.leaveGuards[0](), false)
  assert.equal(cancelled, false)
  const event = { preventDefault() { this.prevented = true } }
  component.state.conversationGuard.beforeUnload(event)
  assert.equal(event.prevented, true)
  accept = true
  assert.equal(await env.leaveGuards[0](), true)
  component.stop()
  await pending
  assert.equal(cancelled, true)
  assert.equal(store.currentProject.id, 'project-1')
})

test('volume terminal ID is consumed before refresh unmounts the editor, so remount does not replay it', async () => {
  let updates = 0
  const done = { ...job('succeeded'), kind: 'volume_smart_split', target: 'plan', payload: {} }
  const env = await runtime(async (url, options) => {
    if (url.includes('/api/jobs?')) return Response.json([])
    if (options.method === 'POST') return Response.json({ job_id: done.id }, { status: 202 })
    if (url.includes('/api/jobs/')) return Response.json(done)
    return Response.json(project)
  })
  const scope = env.vue.effectScope()
  const state = scope.run(() => env.useVolumeJobs(() => 'project-1', () => { updates++; scope.stop() }))
  await settle()
  await state.smartSplit()
  await settle()
  assert.equal(updates, 1)
  assert.deepEqual(JSON.parse(env.storage.getItem('arboris:jobs:project-1:volume_planning')).ids, [])
  const remount = env.vue.effectScope()
  remount.run(() => env.useVolumeJobs(() => 'project-1', () => updates++))
  await settle()
  assert.equal(updates, 1)
  assert.equal(env.calls.filter(call => call.url.endsWith('/api/jobs/job-1')).length, 1)
  remount.stop()
})
