/** Convert explicitly confirmed percentages to the API's per-contract ratios. */
export function marginRatesFromRows(rows) {
  const rates=Object.create(null)
  for (const {symbol:rawSymbol,percent} of rows) {
    const symbol=rawSymbol.trim()
    const empty=percent===undefined || percent===null || percent===''
    if (!symbol && empty) continue
    if (!symbol || /\s/.test(symbol)) throw new Error('请输入有效的保证金合约代码')
    if (typeof percent!=='number' || !Number.isFinite(percent) || percent<=0 || percent>100) {
      throw new Error(`${symbol} 的保证金率必须大于 0 且不超过 100%`)
    }
    if (Object.hasOwn(rates,symbol)) throw new Error(`${symbol} 的保证金率重复填写`)
    rates[symbol]=percent/100
  }
  return rates
}
