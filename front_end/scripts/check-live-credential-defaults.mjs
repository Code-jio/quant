#!/usr/bin/env node

import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse } from 'vue/compiler-sfc'
import ts from 'typescript'

const PROTECTED_FIELDS = [
  'username',
  'password',
  'td_server',
  'md_server',
  'app_id',
  'auth_code',
]

class GateError extends Error {}

function parseFileArgument(argv) {
  const flagIndex = argv.indexOf('--file')
  if (flagIndex === -1) {
    return resolve(process.cwd(), 'src/views/LoginView.vue')
  }

  const candidate = argv[flagIndex + 1]
  if (!candidate || candidate.startsWith('--')) {
    throw new GateError('Missing value for --file')
  }
  return resolve(process.cwd(), candidate)
}

function extractLoginFormObject(source) {
  const { descriptor, errors } = parse(source, { filename: 'LoginView.vue' })
  if (errors.length > 0) {
    throw new GateError('Login view could not be parsed')
  }

  const declarations = []
  for (const block of [descriptor.script, descriptor.scriptSetup].filter(Boolean)) {
    if (block.src) {
      throw new GateError('External login script content is not supported')
    }
    const scriptKinds = {
      js: ts.ScriptKind.JS,
      jsx: ts.ScriptKind.JSX,
      ts: ts.ScriptKind.TS,
      tsx: ts.ScriptKind.TSX,
    }
    const scriptKind = scriptKinds[block.lang || 'js']
    if (typeof scriptKind !== 'number') {
      throw new GateError('Login script language is not supported')
    }
    const script = ts.createSourceFile(
      'login-defaults.ts', block.content, ts.ScriptTarget.Latest, false, scriptKind,
    )
    if (script.parseDiagnostics.length > 0) {
      throw new GateError('Login script could not be parsed')
    }
    for (const statement of script.statements) {
      if (!ts.isVariableStatement(statement)) continue
      for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name) && declaration.name.text === 'form') {
          declarations.push({
            declaration,
            isConst: (statement.declarationList.flags & ts.NodeFlags.Const) !== 0,
          })
        }
      }
    }
  }

  if (declarations.length !== 1 || !declarations[0].isConst) {
    throw new GateError('Expected exactly one top-level const form declaration')
  }
  const initializer = declarations[0].declaration.initializer
  if (
    !initializer || !ts.isCallExpression(initializer) || initializer.questionDotToken ||
    !ts.isIdentifier(initializer.expression) || initializer.expression.text !== 'reactive' ||
    initializer.arguments.length !== 1 || !ts.isObjectLiteralExpression(initializer.arguments[0])
  ) {
    throw new GateError('Login form must directly initialize reactive with an object literal')
  }
  const properties = initializer.arguments[0].properties
  for (const property of properties) {
    if (ts.isSpreadAssignment(property)) {
      throw new GateError('Login form object spread is prohibited')
    }
    if (property.name && ts.isComputedPropertyName(property.name)) {
      throw new GateError('Login form computed properties are prohibited')
    }
    if (
      !ts.isPropertyAssignment(property) ||
      !(ts.isIdentifier(property.name) || ts.isStringLiteral(property.name) || ts.isNumericLiteral(property.name))
    ) {
      throw new GateError('Login form properties must use direct named assignments')
    }
  }
  return properties
}

function inspectDefault(field, matches) {
  if (matches.length !== 1) {
    return `${field}: expected exactly one default assignment`
  }

  const initializer = matches[0].initializer
  if (!ts.isStringLiteral(initializer)) {
    return `${field}: default must be an empty string literal`
  }

  if (initializer.text.length !== 0) {
    return `${field}: non-empty literal default is prohibited`
  }

  return null
}

function main() {
  const filePath = parseFileArgument(process.argv.slice(2))
  if (!existsSync(filePath)) {
    throw new GateError('Login view file was not found')
  }

  const source = readFileSync(filePath, 'utf8')
  const properties = extractLoginFormObject(source)
  const failures = PROTECTED_FIELDS
    .map((field) => inspectDefault(field, properties.filter((property) => property.name.text === field)))
    .filter(Boolean)

  if (failures.length > 0) {
    console.error('Live credential default gate failed.')
    for (const failure of failures) {
      console.error(`- ${failure}`)
    }
    process.exitCode = 1
    return
  }

  console.log('Live credential default gate passed.')
}

try {
  main()
} catch (error) {
  console.error('Live credential default gate could not run.')
  console.error(error instanceof GateError ? error.message : 'Unable to inspect login defaults')
  process.exitCode = 1
}
