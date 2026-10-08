import original from 'file:///D:/mine/quant/front_end/playwright.config.js'

export default {
  ...original,
  testDir: 'D:/mine/quant/front_end/tests/e2e',
  outputDir: 'D:/mine/quant-native-verify-20261008/execution-e2e/results',
  webServer: [{
    ...original.webServer[0],
    command: 'D:/mine/quant/back_end/.venv-live/Scripts/python.exe D:/mine/quant-native-verify-20261008/execution-e2e/backend.py',
    cwd: 'D:/mine/quant/front_end',
    url: 'http://127.0.0.1:18000/health',
  }, {
    ...original.webServer[1],
    cwd: 'D:/mine/quant/front_end',
    command: 'node node_modules/vite/bin/vite.js --config D:/mine/quant-native-verify-20261008/execution-e2e/vite.config.mjs --host 127.0.0.1 --port 55174 --strictPort',
  }],
}
