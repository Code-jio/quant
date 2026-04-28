let echartsPromise

export function loadEcharts() {
  if (!echartsPromise) {
    echartsPromise = import('echarts')
  }
  return echartsPromise
}
