import original from 'file:///D:/mine/quant/front_end/vite.config.js'

export default {
  ...original,
  root: 'D:/mine/quant/front_end',
  server: {
    ...original.server,
    proxy: {
      '/api': {...original.server.proxy['/api'], target:'http://127.0.0.1:18000'},
      '/ws': {...original.server.proxy['/ws'], target:'ws://127.0.0.1:18000'},
    },
  },
}
