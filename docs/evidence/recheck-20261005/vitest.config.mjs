export default {
  root:'D:/mine/quant/front_end',
  resolve:{alias:{
    '@':'D:/mine/quant/front_end/src',
    'vue':'D:/mine/quant/front_end/node_modules/vue/dist/vue.runtime.esm-bundler.js',
    '@vue/test-utils':'D:/mine/quant/front_end/node_modules/@vue/test-utils/dist/vue-test-utils.cjs.js',
    'vitest':'D:/mine/quant/front_end/node_modules/vitest/dist/index.js',
  }},
  test:{environment:'jsdom',include:['D:/mine/quant-recheck-20261005/frontend-probes.spec.js']},
}
