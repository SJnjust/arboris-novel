import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createContext, SourceTextModule, SyntheticModule } from 'node:vm'
import * as vueRouter from 'vue-router'
import ts from 'typescript'
import { runtime, settle } from './runtime.mjs'

const project = () => ({ id: 'project-1', title: '旧馆', chapters: [{ chapter_number: 1, content: '服务端正文', title: '入口', generation_status: 'successful', versions: ['服务端正文'] }], blueprint: { chapter_outline: [{ chapter_number: 1, title: '入口', summary: '调查' }] } })
const json = value => Response.json(value)

test('unsaved guards block route leave/update and beforeunload only while dirty', async () => {
  const env = await runtime(async () => json([]))
  let dirty = false, confirmed = 0
  env.setAlerts({ showConfirm: async () => { confirmed++; return false } })
  const guard = env.guards.useUnsavedChanges(() => dirty)
  assert.equal(await env.leaveGuards[0](), true)
  dirty = true
  assert.equal(await env.leaveGuards[0](), false)
  assert.equal(await env.updateGuards[0](), false)
  const event = { preventDefault() { this.prevented = true }, returnValue: undefined }
  guard.beforeUnload(event)
  assert.equal(event.prevented, true)
  assert.equal(event.returnValue, '')
  assert.equal(confirmed, 2)
  dirty = false
  const clean = { preventDefault() { this.prevented = true } }
  guard.beforeUnload(clean)
  assert.equal(clean.prevented, undefined)
})

test('body editor restores isolated draft, preserves edits on failed save, clears only on server success', async () => {
  const env = await runtime(async () => json([]))
  const key = 'arboris:body-draft:1:project-1:1'
  env.storage.setItem(key, JSON.stringify({ content: '恢复的正文' }))
  env.storage.setItem('arboris:body-draft:1:project-2:1', JSON.stringify({ content: '另一项目正文' }))
  let fail = true, calls = 0, warnings = 0
  const props = { project: project(), selectedChapterNumber: 1, saveContent: async data => {
    calls++; assert.equal(data.content, '继续编辑的正文')
    if (fail) throw new Error('保存暂不可用')
  } }
  let confirm = true
  const component = await env.component('../src/components/writing-desk/WDWorkspace.vue', ['showEditModal', 'editingContent', 'openEditModal', 'closeEditModal', 'saveEditedContent'], {}, props,
    { showConfirm: async () => confirm, showError: () => warnings++ })
  await component.state.openEditModal()
  assert.equal(component.state.editingContent.value, '恢复的正文')
  component.state.editingContent.value = '继续编辑的正文'
  await settle()
  assert.equal(JSON.parse(env.storage.getItem(key)).content, '继续编辑的正文')
  confirm = false
  await component.state.closeEditModal()
  assert.equal(component.state.showEditModal.value, true)
  assert.equal(await env.leaveGuards[0](), false)
  await component.state.saveEditedContent()
  assert.equal(component.state.showEditModal.value, true)
  assert.equal(component.state.editingContent.value, '继续编辑的正文')
  assert.equal(warnings, 1)
  assert.ok(env.storage.getItem(key))
  fail = false
  await component.state.saveEditedContent()
  assert.equal(calls, 2)
  assert.equal(component.state.showEditModal.value, false)
  assert.equal(env.storage.getItem(key), null)
  assert.ok(env.storage.getItem('arboris:body-draft:1:project-2:1'))
  component.stop()
})

test('outline editor close confirmation cancellation keeps input and does not emit close', async () => {
  const env = await runtime(async () => json([]))
  const props = { show: true, chapter: { chapter_number: 1, title: '入口', summary: '调查' } }
  let confirmed = false
  const component = await env.component('../src/components/writing-desk/WDEditChapterModal.vue', ['editableChapter', 'requestClose'], {}, props,
    { showConfirm: async () => confirmed })
  component.state.editableChapter.value.summary = '未保存的新摘要'
  await component.state.requestClose()
  assert.equal(component.emitted.length, 0)
  assert.equal(await env.leaveGuards[0](), false)
  assert.equal(component.state.editableChapter.value.summary, '未保存的新摘要')
  confirmed = true
  await component.state.requestClose()
  assert.equal(component.emitted[0][0], 'close')
  component.stop()
})

test('deleting chapters or regenerating blueprint does nothing when confirmation is cancelled', async () => {
  const env = await runtime(async () => json([]))
  let deletes = 0
  const messages = []
  const store = { currentProject: project(), deleteChapter: async () => deletes++ }
  const alerts = { showConfirm: async message => { messages.push(message); return false } }
  const desk = await env.component('../src/views/WritingDesk.vue', ['deleteChapter'], store, { id: 'project-1' }, alerts)
  await desk.state.deleteChapter([1])
  assert.equal(deletes, 0)
  assert.match(messages[0], /正文、所有版本、评审和大纲/)
  desk.stop()
  const blueprint = await env.component('../src/components/BlueprintDisplay.vue', ['confirmRegenerate'], store, { blueprint: store.currentProject.blueprint }, alerts)
  await blueprint.state.confirmRegenerate()
  assert.equal(blueprint.emitted.length, 0)
  assert.match(messages[1], /未写正文/)
  blueprint.stop()
})

test('volume range confirmation cancellation makes no PATCH and unsaved plan survives a task refresh', async () => {
  const env = await runtime(async () => json([]))
  const props = env.vue.reactive({ projectId: 'project-1', editable: true, blueprint: { total_chapters: 40, volumes: [{ volume_number: 1, title: '原卷名', summary: '原计划', start_chapter: 1, end_chapter: 20, status: 'planned', outline_cursor: 0 }], chapter_outline: [] } })
  const component = await env.component('../src/components/VolumePlanning.vue', ['drafts', 'save', 'smartSplit'], {}, props, { showConfirm: async () => false })
  component.state.drafts.value[1].title = '未保存卷名'
  component.state.drafts.value[1].end_chapter = 21
  props.blueprint = { ...props.blueprint, volumes: [{ ...props.blueprint.volumes[0], status: 'outlined' }] }
  await settle()
  assert.equal(component.state.drafts.value[1].title, '未保存卷名')
  assert.equal(await env.leaveGuards[0](), false)
  await component.state.save(1)
  await component.state.smartSplit()
  assert.equal(env.calls.some(call => call.options.method === 'PATCH' || call.options.method === 'POST'), false)
  component.stop()
})

test('errors never stringify arbitrary objects or expose credentials/internal provider text', async () => {
  const env = await runtime(async () => json([]))
  for (const value of [null, {}, { message: {} }, new Error('provider stack trace'), '[object Object]']) {
    const result = env.errors.errorMessage(value)
    assert.equal(result, '操作失败，请稍后重试')
    assert.equal(result.includes('[object Object]'), false)
  }
  assert.equal(env.errors.errorMessage(new Error('网络暂不可用，请重试')), '网络暂不可用，请重试')
  assert.equal(env.errors.errorMessage({ detail: { message: '所有版本失败', failed_versions: [{ index: 0, detail: '配置有误 api_key=fixture-sensitive；请检查' }] } }).includes('fixture-sensitive'), false)
})

test('real router has 404 fallback and redirects old detail/writing links with query/hash intact', async () => {
  const context = createContext({ console, Error })
  const synthetic = values => new SyntheticModule(Object.keys(values), function () { for (const [name, value] of Object.entries(values)) this.setExport(name, value) }, { context })
  const source = await readFile(new URL('../src/router/index.ts', import.meta.url), 'utf8')
  const module = new SourceTextModule(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText,
    { context, initializeImportMeta: meta => { meta.env = { BASE_URL: '/' } } })
  await module.link(name => name === 'vue-router' ? synthetic({ ...vueRouter, createWebHistory: vueRouter.createMemoryHistory })
    : name === '@/stores/auth' ? synthetic({ useAuthStore: () => ({ isAuthenticated: true, user: {} }) }) : synthetic({ default: {} }))
  await module.evaluate()
  const router = module.namespace.default
  assert.equal(router.resolve('/does-not-exist').name, 'not-found')
  await router.push('/novel/project-1?tab=body#chapter')
  assert.equal(router.currentRoute.value.fullPath, '/novels/project-1/write?tab=body#chapter')
  await router.push('/detail/project-1?tab=outline')
  assert.equal(router.currentRoute.value.fullPath, '/novels/project-1?tab=outline')
})
