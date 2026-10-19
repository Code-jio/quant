import { computeIndicators } from './indicatorMath.js'
self.onmessage = ({data:{id,type,payload}}) => {
  try { self.postMessage({id,result:computeIndicators(type,payload)}) }
  catch(error) { self.postMessage({id,error:error.message}) }
}
