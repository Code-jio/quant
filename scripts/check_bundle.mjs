import {readdirSync,statSync} from 'node:fs'
import {fileURLToPath} from 'node:url'
const dir=fileURLToPath(new URL('../front_end/dist/assets/',import.meta.url))
const budgets={'vendor-echarts':760_000,'vendor-element-plus':910_000}
for (const [prefix,limit] of Object.entries(budgets)) {
  const file=readdirSync(dir).find(f=>f.startsWith(prefix)&&f.endsWith('.js'))
  if(!file)throw new Error(`Missing bundle ${prefix}`)
  const size=statSync(`${dir}/${file}`).size
  console.log(`${prefix}: ${size} / ${limit} bytes`)
  if(size>limit)process.exitCode=1
}
