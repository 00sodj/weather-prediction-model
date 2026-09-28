# -*- coding: utf-8 -*-
"""生成独立 HTML 文件 — 包含完整一年历史 + 24h六项气象分析"""
import os, json

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_outputs")

with open(os.path.join(MODEL_DIR, "arima_results.json"), "r", encoding="utf-8") as f:
    results = json.load(f)
with open(os.path.join(MODEL_DIR, "training_summary.json"), "r", encoding="utf-8") as f:
    summary = json.load(f)

hourly = {}
hourly_path = os.path.join(MODEL_DIR, "hourly_analysis.json")
if os.path.isfile(hourly_path):
    with open(hourly_path, "r", encoding="utf-8") as f:
        hourly = json.load(f)

results_json = json.dumps(results, ensure_ascii=False)
summary_json = json.dumps(summary, ensure_ascii=False)
hourly_json = json.dumps(hourly, ensure_ascii=False)

# 数据截止日：取所有城市里最晚的那天，显示在页头。
# 报告每天自动更新，没有这个日期的话访客无法判断看到的是不是今天的数据。
_history_last = [
    v["history"]["dates"][-1]
    for v in results.values()
    if isinstance(v, dict) and v.get("history", {}).get("dates")
]
data_through = max(_history_last) if _history_last else "未知"

html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>天气温度预测系统 - ARIMA时间序列模型</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0c1117;--card:#161b22;--border:#30363d;--text:#e6edf3;--text2:#8b949e;--text3:#6e7681;--accent:#58a6ff;--green:#3fb950;--orange:#d29922;--red:#f85149;--purple:#bc8cff}
body{font-family:system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{background:linear-gradient(180deg,rgba(22,27,34,.95),var(--bg));border-bottom:1px solid var(--border);padding:24px 0;position:sticky;top:0;z-index:100;backdrop-filter:blur(12px)}
.header-inner{max-width:1600px;margin:0 auto;padding:0 32px;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:14px}
.logo-icon{width:40px;height:40px;background:linear-gradient(135deg,var(--accent),#1f6feb);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px}
.logo h1{font-size:20px;font-weight:500;letter-spacing:-.5px}
.logo h1 span{color:var(--accent);font-weight:700}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:12px;font-family:Consolas,monospace}
.badge-blue{background:rgba(88,166,255,.12);color:var(--accent);border:1px solid rgba(88,166,255,.2)}
.badge-green{background:rgba(63,185,80,.12);color:var(--green);border:1px solid rgba(63,185,80,.2)}
.badge-orange{background:rgba(210,153,34,.12);color:var(--orange);border:1px solid rgba(210,153,34,.2)}
.badge-red{background:rgba(248,81,73,.12);color:var(--red);border:1px solid rgba(248,81,73,.2)}
.badge-purple{background:rgba(188,140,255,.12);color:var(--purple);border:1px solid rgba(188,140,255,.2)}
.container{max-width:1600px;margin:0 auto;padding:24px 32px}
.stats-row{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:24px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;transition:border-color .2s}
.stat-card:hover{border-color:var(--accent)}
.stat-label{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.stat-value{font-size:28px;font-weight:700;font-family:Consolas,monospace}
.stat-value.blue{color:var(--accent)}.stat-value.green{color:var(--green)}.stat-value.orange{color:var(--orange)}
.main-grid{display:grid;grid-template-columns:300px 1fr;gap:20px}
.city-panel{background:var(--card);border:1px solid var(--border);border-radius:12px;height:calc(100vh - 260px);display:flex;flex-direction:column}
.search-box{padding:16px;border-bottom:1px solid var(--border)}
.search-box input{width:100%;padding:10px 14px;background:#0d1117;border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;outline:none;transition:border-color .2s}
.search-box input:focus{border-color:var(--accent)}
.search-box input::placeholder{color:var(--text3)}
.city-list{flex:1;overflow-y:auto;padding:8px}
.city-list::-webkit-scrollbar{width:6px}.city-list::-webkit-scrollbar-track{background:transparent}.city-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.city-item{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;cursor:pointer;transition:background .15s;font-size:14px}
.city-item:hover{background:rgba(88,166,255,.06)}
.city-item.active{background:rgba(88,166,255,.1);border-left:3px solid var(--accent)}
.city-item .mae{font-family:Consolas,monospace;font-size:12px}
.chart-panel{display:flex;flex-direction:column;gap:16px;overflow-y:auto;max-height:calc(100vh - 200px);padding-right:4px}
.chart-panel::-webkit-scrollbar{width:6px}.chart-panel::-webkit-scrollbar-track{background:transparent}.chart-panel::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.chart-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
.chart-title{font-size:15px;font-weight:500;margin-bottom:4px;display:flex;align-items:center;gap:8px}
.chart-subtitle{font-size:12px;color:var(--text2);margin-bottom:16px}
.chart-container{width:100%;height:350px}
.chart-container-sm{width:100%;height:260px}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.info-item{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(48,54,61,.5)}
.info-item:last-child{border:none}
.info-key{color:var(--text2);font-size:13px}
.info-val{font-family:Consolas,monospace;font-size:13px;font-weight:500}
.forecast-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
.fc-card{background:#0d1117;border:1px solid var(--border);border-radius:10px;padding:16px;text-align:center}
.fc-date{font-size:12px;color:var(--text2);margin-bottom:6px}
.fc-temp{font-size:32px;font-weight:700;color:var(--accent);font-family:Consolas,monospace}
.fc-range{font-size:11px;color:var(--text3);margin-top:4px;font-family:Consolas,monospace}
.hourly-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.tab-row{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.tab-btn{padding:6px 16px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--text2);font-size:13px;cursor:pointer;transition:all .2s}
.tab-btn:hover{border-color:var(--accent);color:var(--text)}
.tab-btn.active{background:rgba(88,166,255,.15);border-color:var(--accent);color:var(--accent)}
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;height:400px;color:var(--text3)}
.empty-state svg{width:64px;height:64px;margin-bottom:16px;opacity:.3}
.empty-state p{font-size:14px}
.stat-mini{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:6px;font-size:11px;font-family:Consolas,monospace;background:rgba(255,255,255,.04);margin:2px}
@media(max-width:1024px){.stats-row{grid-template-columns:repeat(3,1fr)}.main-grid{grid-template-columns:1fr}.city-panel{height:300px}.hourly-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
  <div class="header-inner">
    <div class="logo"><div class="logo-icon">🌡</div><h1>天气温度<span>预测系统</span></h1></div>
    <div style="display:flex;gap:8px">
      <span class="badge badge-blue">ARIMA 时间序列模型</span>
      <span class="badge badge-orange">数据截至 __DATA_THROUGH__</span>
      <span class="badge badge-green" id="badge-mae">加载中...</span>
    </div>
  </div>
</div>
<div class="container">
  <div class="stats-row">
    <div class="stat-card"><div class="stat-label">训练城市</div><div class="stat-value blue" id="s-cities">-</div></div>
    <div class="stat-card"><div class="stat-label">平均 MAE</div><div class="stat-value green" id="s-mae">-</div></div>
    <div class="stat-card"><div class="stat-label">中位 MAE</div><div class="stat-value green" id="s-med">-</div></div>
    <div class="stat-card"><div class="stat-label">MAE≤2°C 占比</div><div class="stat-value orange" id="s-pct">-</div></div>
    <div class="stat-card"><div class="stat-label">预测天数</div><div class="stat-value blue">3</div></div>
  </div>
  <div class="main-grid">
    <div class="city-panel">
      <div class="search-box"><input type="text" id="city-search" placeholder="搜索城市..."></div>
      <div class="city-list" id="city-list"></div>
    </div>
    <div class="chart-panel" id="detail-panel">
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 12h4l3-9 4 18 3-9h4"/></svg>
        <p>选择左侧城市查看预测详情</p>
      </div>
    </div>
  </div>
</div>
<script>
const RESULTS = ''' + results_json + ''';
const SUMMARY = ''' + summary_json + ''';
const HOURLY = ''' + hourly_json + ''';

const PARAM_META = {
  temperature:{label:'气温',unit:'°C',color:'#58a6ff'},
  humidity:{label:'相对湿度',unit:'%',color:'#3fb950'},
  pressure:{label:'气压',unit:'hPa',color:'#bc8cff'},
  precipitation:{label:'降水',unit:'mm',color:'#79c0ff'},
  wind_speed:{label:'风速',unit:'km/h',color:'#d29922'},
  wind_direction:{label:'风向',unit:'°',color:'#f0883e'}
};

let charts=[];

document.getElementById('s-cities').textContent=SUMMARY.success||'-';
document.getElementById('s-mae').textContent=SUMMARY.avg_mae?SUMMARY.avg_mae.toFixed(2)+'°C':'-';
document.getElementById('s-med').textContent=SUMMARY.median_mae?SUMMARY.median_mae.toFixed(2)+'°C':'-';
document.getElementById('s-pct').textContent=SUMMARY.pct_mae_le_2?SUMMARY.pct_mae_le_2.toFixed(1)+'%':'-';
const bdg=document.getElementById('badge-mae');
if(SUMMARY.avg_mae<=2){bdg.textContent='✓ 平均MAE='+SUMMARY.avg_mae.toFixed(2)+'°C 达标';bdg.className='badge badge-green';}

let allCities=Object.entries(RESULTS).map(([n,d])=>({name:n,mae:d.metrics.MAE})).sort((a,b)=>a.name.localeCompare(b.name,'zh'));
renderCityList(allCities);

function renderCityList(cities){
  document.getElementById('city-list').innerHTML=cities.map(c=>{
    const cls=c.mae<=2?'green':c.mae<=3?'orange':'red';
    return '<div class="city-item" data-city="'+c.name+'" onclick="selectCity(this.dataset.city)"><span>'+c.name+'</span><span class="mae badge badge-'+cls+'">'+c.mae.toFixed(2)+'°C</span></div>';
  }).join('');
}
document.getElementById('city-search').addEventListener('input',function(){
  const q=this.value.toLowerCase();
  renderCityList(allCities.filter(c=>c.name.includes(q)));
});

function selectCity(name){
  document.querySelectorAll('.city-item').forEach(el=>el.classList.toggle('active',el.dataset.city===name));
  const d=RESULTS[name];if(!d)return;
  const h=HOURLY[name];
  const mc=d.metrics.MAE<=2?'green':d.metrics.MAE<=3?'orange':'red';

  let hourlyHTML='';
  if(h){
    hourlyHTML=`
    <div class="chart-card">
      <div class="chart-title">过去24小时气象数据 <span class="badge badge-purple">6项指标</span></div>
      <div class="chart-subtitle">气温 · 湿度 · 气压 · 降水 · 风速 · 风向 — 逐小时数据</div>
      <div class="tab-row" id="hourly-tabs"></div>
      <div class="hourly-grid" id="hourly-charts">
        <div class="chart-container-sm" id="hc-temperature"></div>
        <div class="chart-container-sm" id="hc-humidity"></div>
        <div class="chart-container-sm" id="hc-pressure"></div>
        <div class="chart-container-sm" id="hc-precipitation"></div>
        <div class="chart-container-sm" id="hc-wind_speed"></div>
        <div class="chart-container-sm" id="hc-wind_direction"></div>
      </div>
      <div style="margin-top:16px;display:grid;grid-template-columns:repeat(3,1fr);gap:12px" id="hourly-stats"></div>
    </div>`;
  }

  const panel=document.getElementById('detail-panel');
  panel.innerHTML=`
    <div class="chart-card">
      <div class="chart-title">${d.city} — 过去一年历史温度（完整365天）</div>
      <div class="chart-subtitle">ARIMA(${d.order.join(',')}) | AIC=${d.aic} | 数据含日均温、日最高/最低温</div>
      <div class="chart-container" id="chart-history"></div>
    </div>
    <div class="chart-card">
      <div class="chart-title">测试集回测 + 未来3天预测 <span class="badge badge-${mc}">MAE=${d.metrics.MAE.toFixed(2)}°C</span></div>
      <div class="chart-subtitle">最近${d.metrics.test_days}天回测对比 + 未来3天外推预测（含95%置信区间）</div>
      <div class="chart-container" id="chart-forecast"></div>
    </div>
    ${hourlyHTML}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="chart-card">
        <div class="chart-title">模型精度指标</div>
        <div class="info-grid">
          <div class="info-item"><span class="info-key">MAE</span><span class="info-val" style="color:var(--${mc})">${d.metrics.MAE.toFixed(4)}°C</span></div>
          <div class="info-item"><span class="info-key">RMSE</span><span class="info-val">${d.metrics.RMSE.toFixed(4)}°C</span></div>
          <div class="info-item"><span class="info-key">MAPE</span><span class="info-val">${d.metrics.MAPE.toFixed(2)}%</span></div>
          <div class="info-item"><span class="info-key">ARIMA阶数</span><span class="info-val">(${d.order.join(', ')})</span></div>
          <div class="info-item"><span class="info-key">AIC</span><span class="info-val">${d.aic}</span></div>
          <div class="info-item"><span class="info-key">ADF p值</span><span class="info-val">${d.adf_test.p_value}</span></div>
          <div class="info-item"><span class="info-key">平稳性</span><span class="info-val">${d.adf_test.is_stationary?'✓ 平稳':'✗ 非平稳'}</span></div>
          <div class="info-item"><span class="info-key">测试天数</span><span class="info-val">${d.metrics.test_days}天</span></div>
        </div>
      </div>
      <div class="chart-card">
        <div class="chart-title">未来3天温度预测</div>
        <div class="forecast-cards">
          ${d.forecast.dates.map((dt,i)=>'<div class="fc-card"><div class="fc-date">'+dt+'</div><div class="fc-temp">'+d.forecast.values[i]+'°</div><div class="fc-range">'+d.forecast.lower[i]+'° ~ '+d.forecast.upper[i]+'°</div></div>').join('')}
        </div>
      </div>
    </div>`;

  charts.forEach(c=>{try{c.dispose();}catch(e){}});
  charts=[];
  renderHistoryChart(d);
  renderForecastChart(d);
  if(h) renderHourlyCharts(h,name);
}

function renderHistoryChart(d){
  const el=document.getElementById('chart-history');if(!el)return;
  const c=echarts.init(el,'dark');charts.push(c);
  c.setOption({
    backgroundColor:'transparent',
    tooltip:{trigger:'axis',backgroundColor:'#161b22',borderColor:'#30363d',textStyle:{color:'#e6edf3',fontSize:12}},
    legend:{data:['日均气温','模型拟合'],top:5,textStyle:{color:'#8b949e'}},
    grid:{left:50,right:30,top:40,bottom:50},
    xAxis:{type:'category',data:d.history.dates,axisLabel:{color:'#6e7681',fontSize:10,interval:Math.floor(d.history.dates.length/10)},axisLine:{lineStyle:{color:'#30363d'}}},
    yAxis:{type:'value',name:'°C',axisLabel:{color:'#6e7681'},splitLine:{lineStyle:{color:'#21262d'}}},
    series:[
      {name:'日均气温',type:'line',data:d.history.values,smooth:true,lineStyle:{width:1.5,color:'#58a6ff'},itemStyle:{color:'#58a6ff'},symbol:'none',areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(88,166,255,.15)'},{offset:1,color:'rgba(88,166,255,0)'}]}}},
      {name:'模型拟合',type:'line',data:d.fitted.values,smooth:true,lineStyle:{width:1.5,color:'#f0883e',type:'dashed'},itemStyle:{color:'#f0883e'},symbol:'none'}
    ],
    dataZoom:[{type:'inside'},{type:'slider',height:20,bottom:5,textStyle:{color:'#6e7681'}}]
  });
  window.addEventListener('resize',()=>c.resize());
}

function renderForecastChart(d){
  const el=document.getElementById('chart-forecast');if(!el)return;
  const c=echarts.init(el,'dark');charts.push(c);
  const allDates=[...d.test.dates,...d.forecast.dates];
  const actual=[...d.test.actual,...Array(d.forecast.dates.length).fill(null)];
  const pred=[...d.test.predicted,...d.forecast.values];
  const lo=[...Array(d.test.dates.length).fill(null),...d.forecast.lower];
  const hi=[...Array(d.test.dates.length).fill(null),...d.forecast.upper];
  c.setOption({
    backgroundColor:'transparent',
    tooltip:{trigger:'axis',backgroundColor:'#161b22',borderColor:'#30363d',textStyle:{color:'#e6edf3',fontSize:12}},
    legend:{data:['实际值','预测值','95%上限','95%下限'],top:5,textStyle:{color:'#8b949e',fontSize:11}},
    grid:{left:50,right:30,top:40,bottom:30},
    xAxis:{type:'category',data:allDates,axisLabel:{color:'#6e7681',fontSize:10,rotate:30},axisLine:{lineStyle:{color:'#30363d'}}},
    yAxis:{type:'value',name:'°C',axisLabel:{color:'#6e7681'},splitLine:{lineStyle:{color:'#21262d'}}},
    series:[
      {name:'实际值',type:'line',data:actual,lineStyle:{width:2,color:'#58a6ff'},itemStyle:{color:'#58a6ff'},symbol:'circle',symbolSize:4},
      {name:'预测值',type:'line',data:pred,lineStyle:{width:2,color:'#3fb950'},itemStyle:{color:'#3fb950'},symbol:'circle',symbolSize:4},
      {name:'95%上限',type:'line',data:hi,lineStyle:{width:1,color:'rgba(63,185,80,.3)',type:'dashed'},itemStyle:{color:'rgba(63,185,80,.3)'},symbol:'none'},
      {name:'95%下限',type:'line',data:lo,lineStyle:{width:1,color:'rgba(63,185,80,.3)',type:'dashed'},itemStyle:{color:'rgba(63,185,80,.3)'},symbol:'none'}
    ]
  });
  window.addEventListener('resize',()=>c.resize());
}

function renderHourlyCharts(h, cityName){
  const times=h.times||[];
  const shortTimes=times.map(t=>t.split(' ')[1]||t);
  const params=['temperature','humidity','pressure','precipitation','wind_speed','wind_direction'];
  let statsHTML='';

  params.forEach(key=>{
    const meta=PARAM_META[key];
    const pdata=h[key];
    if(!pdata)return;
    const el=document.getElementById('hc-'+key);
    if(!el)return;
    const c=echarts.init(el,'dark');charts.push(c);
    const vals=pdata.values||[];
    const st=pdata.stats||{};

    const isBar=(key==='precipitation');
    c.setOption({
      backgroundColor:'transparent',
      title:{text:meta.label,textStyle:{color:'#e6edf3',fontSize:13,fontWeight:500},left:10,top:5},
      tooltip:{trigger:'axis',backgroundColor:'#161b22',borderColor:'#30363d',textStyle:{color:'#e6edf3',fontSize:11},formatter:function(p){return p[0].axisValue+'<br/>'+meta.label+': '+(p[0].value!=null?p[0].value:'-')+' '+meta.unit}},
      grid:{left:50,right:15,top:35,bottom:25},
      xAxis:{type:'category',data:shortTimes,axisLabel:{color:'#6e7681',fontSize:9,interval:3},axisLine:{lineStyle:{color:'#30363d'}}},
      yAxis:{type:'value',name:meta.unit,nameTextStyle:{fontSize:10,color:'#6e7681'},axisLabel:{color:'#6e7681',fontSize:10},splitLine:{lineStyle:{color:'#21262d'}}},
      series:[{
        type:isBar?'bar':'line',
        data:vals,
        smooth:!isBar,
        lineStyle:isBar?undefined:{width:2,color:meta.color},
        itemStyle:{color:meta.color},
        symbol:isBar?undefined:'none',
        areaStyle:isBar?undefined:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:meta.color.replace(')',',0.15)').replace('rgb','rgba')},{offset:1,color:'transparent'}]}}
      }]
    });
    window.addEventListener('resize',()=>c.resize());

    statsHTML+=`<div style="background:#0d1117;border:1px solid var(--border);border-radius:10px;padding:14px">
      <div style="font-size:13px;color:${meta.color};margin-bottom:8px;font-weight:500">${meta.label}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:12px">
        <span class="stat-mini">均值: ${st.mean!=null?st.mean:'-'} ${meta.unit}</span>
        <span class="stat-mini">标准差: ${st.std!=null?st.std:'-'} ${meta.unit}</span>
        <span class="stat-mini">最高: ${st.max!=null?st.max:'-'} ${meta.unit}</span>
        <span class="stat-mini">最低: ${st.min!=null?st.min:'-'} ${meta.unit}</span>
      </div>
    </div>`;
  });

  const statsEl=document.getElementById('hourly-stats');
  if(statsEl) statsEl.innerHTML=statsHTML;
}
</script>
</body>
</html>'''

output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "天气温度预测系统.html")

# 用 replace 而不是 f-string 注入 —— 模板里有大量 CSS 花括号，f-string 会被当成占位符
html = html.replace("__DATA_THROUGH__", data_through)

with open(output, "w", encoding="utf-8") as f:
    f.write(html)

size_mb = os.path.getsize(output) / 1024 / 1024
print(f"已生成: {output}")
print(f"文件大小: {size_mb:.1f} MB")
print(f"包含: {len(results)} 城市ARIMA结果 + {len(hourly)} 城市24h气象分析")
