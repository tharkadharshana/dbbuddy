#!/usr/bin/env node
/**
 * check-hook-order.mjs — catch a React hook placed after an early return.
 *
 * A hook below a conditional `return` runs on some renders and not others.
 * React tears the whole tree down when the count changes, and the symptom is
 * a blank screen rather than an error in the place that caused it, so this is
 * expensive to debug by hand. It cost a debugging session in the embed widget.
 *
 * There is no eslint in this project. Rather than add one for a single rule,
 * this is a text scan: crude, no dependencies, and it catches the shape that
 * actually bit us.
 *
 *   node scripts/check-hook-order.mjs            # whole src tree
 *   node scripts/check-hook-order.mjs src/embed  # one directory
 *
 * Exits non-zero on a finding.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const HOOK = /^\s*(?:const .*=\s*)?(useState|useEffect|useRef|useMemo|useCallback|useLayoutEffect|useReducer|useContext)\s*\(/
const COMPONENT = /^(?:export default )?function\s+([A-Z]\w*)\s*\(/
// A conditional return inside a component body sits at 2 or 4 spaces -- 4 when
// it is wrapped in an `if`, which is the common shape. Cleanup returns inside
// useEffect look identical at that indent, so exclude the two forms they take.
const EARLY_RETURN = /^ {2,4}return[\s(;]/
const CLEANUP_RETURN = /^\s*return\s*(\(\s*\)\s*=>|function)/

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.jsx?$/.test(name)) out.push(p)
  }
  return out
}

const root = process.argv[2] || 'src'
const findings = []

for (const file of walk(root)) {
  const lines = readFileSync(file, 'utf8').split('\n')
  let component = null
  let returnedAt = 0
  lines.forEach((line, i) => {
    const c = COMPONENT.exec(line)
    if (c) { component = c[1]; returnedAt = 0; return }
    if (!component) return
    if (EARLY_RETURN.test(line) && !CLEANUP_RETURN.test(line) && !returnedAt)
      returnedAt = i + 1
    if (returnedAt && HOOK.test(line)) {
      findings.push({ file, line: i + 1, component, returnedAt, text: line.trim().slice(0, 70) })
      returnedAt = 0   // one report per component is enough
    }
  })
}

if (findings.length === 0) {
  console.log('hook order OK')
  process.exit(0)
}

for (const f of findings) {
  console.error(
    `${f.file}:${f.line}  ${f.component}() has a hook after the return on line ` +
    `${f.returnedAt}\n    ${f.text}`
  )
}
console.error(
  `\n${findings.length} finding(s). Move the hook above every conditional return.`
)
process.exit(1)
