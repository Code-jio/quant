import policy from '../../../shared/domestic_futures_policy.json'

export const DOMESTIC_EXCHANGES = policy.exchanges

// This is the search scope, not verification of account trading permissions.
export function isDomesticFuture(contract) {
  if (!contract || typeof contract !== 'object') return false
  const exchange = String(contract.exchange ?? '').toUpperCase()
  if (!DOMESTIC_EXCHANGES.includes(exchange) || contract.product !== 'FUTURES') return false
  if (contract.source === 'historical' || contract.tradable === false || contract.is_trading === false) return false
  const match = /^([A-Za-z]+)([0-9]{3,4})$/.exec(String(contract.symbol ?? ''))
  if (!match || Number(match[2].slice(-2)) < 1 || Number(match[2].slice(-2)) > 12) return false
  if (exchange !== 'CZCE' && match[2].length !== 4) return false
  return exchange !== 'CFFEX' || policy.cffex_futures.includes(match[1].toUpperCase())
}

export function domesticFutures(contracts) {
  return Array.isArray(contracts) ? contracts.filter(isDomesticFuture) : []
}
