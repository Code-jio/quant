import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'

const script = resolve('scripts/check-live-credential-defaults.mjs')
const fields = ['username', 'password', 'td_server', 'md_server', 'app_id', 'auth_code']
const sentinel = 'MUST_NOT_ECHO_CREDENTIAL_VALUE'
const emptyValues = () => Object.fromEntries(fields.map((field) => [field, '']))

function createLoginView(values) {
  return `<script setup>\nconst form = reactive({\n${fields
    .map((field) => `  ${field}: ${JSON.stringify(values[field])},`)
    .join('\n')}\n})\n</script>\n`
}

function runGate(source) {
  const fixtureDirectory = mkdtempSync(join(tmpdir(), 'quant-live-default-gate-'))
  const fixturePath = join(fixtureDirectory, 'LoginView.vue')
  writeFileSync(fixturePath, source, 'utf8')

  try {
    return spawnSync(process.execPath, [script, '--file', fixturePath], {
      cwd: resolve('.'),
      encoding: 'utf8',
    })
  } finally {
    rmSync(fixtureDirectory, { recursive: true, force: true })
  }
}

test('accepts an empty literal for every protected live-login default', () => {
  const result = runGate(createLoginView(emptyValues()))

  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /passed/i)
})

function assertRejected(source, diagnostic) {
  const result = runGate(source)
  const output = `${result.stdout}${result.stderr}`

  assert.equal(result.status, 1, output)
  if (diagnostic) assert.match(output, diagnostic)
  assert.doesNotMatch(output, new RegExp(sentinel))
}

for (const field of fields) {
  test(`rejects non-empty ${field} without echoing its value`, () => {
    assertRejected(createLoginView({ ...emptyValues(), [field]: sentinel }), new RegExp(field))
  })
}

test('ignores a commented empty form before the real non-empty form', () => {
  const emptyForm = createLoginView(emptyValues()).replace(/<\/?script[^>]*>/g, '')
  const source = createLoginView({ ...emptyValues(), password: sentinel })
    .replace('<script setup>', `<script setup>\n/* ${emptyForm} */`)

  assertRejected(source, /password/)
})

test('ignores a template comment decoy before the real form', () => {
  const emptyForm = createLoginView(emptyValues()).replace(/<\/?script[^>]*>/g, '')
  assertRejected(
    `<template><!-- ${emptyForm} --><div /></template>\n${createLoginView({ ...emptyValues(), password: sentinel })}`,
    /password/,
  )
})

test('rejects object spread overriding empty defaults', () => {
  const source = createLoginView(emptyValues())
    .replace('<script setup>', `<script setup>\nconst defaults = { password: '${sentinel}' }`)
    .replace('\n})', '\n...defaults,\n})')

  assertRejected(source, /spread/i)
})

test('rejects computed properties even after empty protected defaults', () => {
  assertRejected(
    createLoginView(emptyValues()).replace('\n})', `\n['password']: '${sentinel}',\n})`),
    /computed/i,
  )
})

test('accepts formatting changes and quoted property names in TypeScript setup', () => {
  const source = createLoginView(emptyValues())
    .replace('<script setup>', '<script setup lang="ts">')
    .replace('const form = reactive({', 'const form =\nreactive ( {')
    .replace('password:', '"password":')
  const result = runGate(source)

  assert.equal(result.status, 0, result.stderr)
})

test('accepts a direct top-level form in a normal script block', () => {
  const result = runGate(createLoginView(emptyValues()).replace('<script setup>', '<script>'))
  assert.equal(result.status, 0, result.stderr)
})

test('rejects missing protected fields', () => {
  assertRejected(createLoginView(emptyValues()).replace('  password: "",\n', ''), /password/)
})

test('rejects duplicate protected fields', () => {
  assertRejected(createLoginView(emptyValues()).replace('\n})', '\npassword: "",\n})'), /password/)
})

test('rejects expression defaults even when the expression can return an empty string', () => {
  assertRejected(createLoginView(emptyValues()).replace('password: ""', 'password: String()'), /password/)
})

test('rejects a form initialized from a variable rather than a direct object', () => {
  const source = createLoginView(emptyValues())
    .replace('const form = reactive({', 'const defaults = {')
    .replace('\n})', '\n}\nconst form = reactive(defaults)')
  assertRejected(source)
})

test('rejects a non-const form declaration', () => {
  assertRejected(createLoginView(emptyValues()).replace('const form', 'let form'))
})

test('rejects form declarations present in both script blocks', () => {
  assertRejected(
    createLoginView(emptyValues()).replace('<script setup>', '<script>') + createLoginView(emptyValues()),
  )
})

test('rejects a nested-only form declaration', () => {
  const source = createLoginView(emptyValues())
    .replace('<script setup>', '<script setup>\nfunction nested() {')
    .replace('</script>', '}\n</script>')
  assertRejected(source)
})

test('rejects missing inline script blocks', () => {
  assertRejected('<template><div /></template>')
})

test('rejects invalid script syntax without printing source text', () => {
  assertRejected(createLoginView(emptyValues()).replace('</script>', `const ${sentinel} = ;\n</script>`))
})

test('rejects malformed Vue files without printing source text', () => {
  assertRejected(`${createLoginView(emptyValues())}<script setup>\n${sentinel}`)
})

test('rejects external script content that cannot be inspected', () => {
  assertRejected(`<script src="./${sentinel}.js"></script>\n${createLoginView(emptyValues())}`)
})
