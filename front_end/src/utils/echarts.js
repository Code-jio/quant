import * as echarts from 'echarts/core'
import {LineChart,BarChart,CandlestickChart,PieChart,ScatterChart,HeatmapChart} from 'echarts/charts'
import {GridComponent,LegendComponent,TooltipComponent,DataZoomComponent,MarkPointComponent,
  MarkLineComponent,MarkAreaComponent,VisualMapComponent,GraphicComponent,TitleComponent,ToolboxComponent} from 'echarts/components'
import {CanvasRenderer} from 'echarts/renderers'
echarts.use([LineChart,BarChart,CandlestickChart,PieChart,ScatterChart,HeatmapChart,GridComponent,
  LegendComponent,TooltipComponent,DataZoomComponent,MarkPointComponent,MarkLineComponent,MarkAreaComponent,
  VisualMapComponent,GraphicComponent,TitleComponent,ToolboxComponent,CanvasRenderer])
export * from 'echarts/core'
