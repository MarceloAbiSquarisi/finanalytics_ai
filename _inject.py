f = r'src/finanalytics_ai/interfaces/api/static/dashboard.html'
c = open(f, encoding='utf-8').read()
SEP = '''
var _daySepTimes=[];var _daySepSvg=null;
function initDaySeparators(bars){
  _daySepTimes=[];
  for(var i=1;i<bars.length;i++){if(new Date(bars[i-1].time*1000).toDateString()!==new Date(bars[i].time*1000).toDateString())_daySepTimes.push(bars[i].time);}
  var ct=document.getElementById('chart-price');
  var old=ct.querySelector('.day-sep-svg');if(old)old.remove();
  _daySepSvg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  _daySepSvg.classList.add('day-sep-svg');
  _daySepSvg.setAttribute('style','position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2');
  ct.style.position='relative';ct.appendChild(_daySepSvg);
  drawDaySeparators();
  priceChart.timeScale().subscribeVisibleLogicalRangeChange(function(){setTimeout(drawDaySeparators,16);});
}
function drawDaySeparators(){
  if(!_daySepSvg||!priceChart||!_daySepTimes.length)return;
  var h=_daySepSvg.parentElement.clientHeight||400;
  while(_daySepSvg.firstChild)_daySepSvg.removeChild(_daySepSvg.firstChild);
  _daySepTimes.forEach(function(t){
    var x=priceChart.timeScale().timeToCoordinate(t);
    if(x===null||x<0)return;
    var l=document.createElementNS('http://www.w3.org/2000/svg','line');
    l.setAttribute('x1',x);l.setAttribute('x2',x);l.setAttribute('y1',0);l.setAttribute('y2',h);
    l.setAttribute('stroke','rgba(255,255,255,0.15)');l.setAttribute('stroke-width','1');l.setAttribute('stroke-dasharray','4,4');
    _daySepSvg.appendChild(l);
  });
}
'''
TARGET='async function loadChartAndIndicators('
SETDATA='priceSeries.setData(bars);'
if TARGET in c and SETDATA in c:
    c=c.replace(TARGET,SEP+'\n'+TARGET)
    c=c.replace(SETDATA,SETDATA+'\n  initDaySeparators(bars);')
    open(f,'w',encoding='utf-8').write(c)
    print('OK initDaySeparators:',c.count('initDaySeparators'))
else:
    print('TARGET NAO ENCONTRADO')
