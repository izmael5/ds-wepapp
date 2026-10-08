(() => {
  const $ = (id) => document.getElementById(id);
  let chart = null;
  let toastTimer;
  const labels = { total_orders:'Total orders', valid_orders:'Valid orders', unique_customers:'Customers', total_revenue:'Net revenue', total_profit:'Total profit', avg_order_value:'Avg. order value', avg_rating:'Customer rating' };
  const moneyKeys = new Set(['total_revenue','total_profit','avg_order_value']);
  const colors = ['#3f83ed','#63b7ef','#55c7a7','#f1b85b','#9a83e8','#ee8291','#55a9a4','#8ca0b8'];
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const toast = (message) => { const el=$('toast'); el.textContent=message; el.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('show'),2800); };
  const setStatus = (id, message, kind='') => { const el=$(id); el.textContent=message; el.className=`form-status ${kind}`; };
  const request = async (url, options={}) => { const method=(options.method||'GET').toUpperCase(); const headers=new Headers(options.headers||{}); if(method!=='GET')headers.set('X-CSRFToken',csrf()); const res=await fetch(url,{...options,headers});let data={};try{data=await res.json()}catch{}if(!res.ok)throw new Error(data.error||`Request failed (${res.status})`);return data; };
  const formatNumber = (n, money=false) => { const value=Number(n)||0; return money ? new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value) : new Intl.NumberFormat(undefined,{maximumFractionDigits:2}).format(value); };

  async function refreshStatus(){
    const s=await request('/api/status'); $('dataStatus').textContent=s.loaded?`${formatNumber(s.rows)} of ${formatNumber(s.total_rows)} rows · ${s.columns.length} fields`:'No data loaded';
    $('rowCount').textContent=`${formatNumber(s.rows)} rows`;
    $('chartEmpty').style.display=s.loaded?'none':'flex';
    document.querySelectorAll('.export-link').forEach(a=>{a.style.opacity=s.loaded?'1':'.5';a.setAttribute('aria-disabled',String(!s.loaded));});
    return s;
  }
  async function loadKpis(){
    try { const k=await request('/kpis'); const order=['total_orders','total_revenue','total_profit','unique_customers','avg_order_value','avg_rating'];
      $('kpiGrid').innerHTML=order.map((key,i)=>`<article class="kpi-card"><div class="kpi-head"><span>${labels[key]}</span><span class="metric-symbol">${['▦','$','↗','♙','◷','☆'][i]}</span></div><div class="kpi-value">${moneyKeys.has(key)?formatNumber(k[key],true):formatNumber(k[key])}</div><div class="kpi-note">${key==='total_orders'?`${formatNumber(k.valid_orders)} valid orders`:key==='total_revenue'||key==='total_profit'?'Excludes cancelled orders':key==='unique_customers'?'Unique customer IDs':key==='avg_order_value'?'Average revenue per order':'Average available rating'}</div></article>`).join('');
    } catch(e) { /* No data yet is a normal initial state. */ }
  }
  async function loadColumns(){
    try { const d=await request('/api/columns'); const select=$('chartColumn'); const preferred=['region','product_category','sales_rep','customer_segment','sales_channel','payment_method','source']; const ordered=[...preferred.filter(c=>d.columns.includes(c)),...d.columns.filter(c=>!preferred.includes(c))]; const old=select.value; select.innerHTML=ordered.map(c=>`<option value="${escapeHtml(c)}">${escapeHtml(c.replaceAll('_',' ').replace(/\b\w/g,x=>x.toUpperCase()))}</option>`).join(''); if(ordered.includes(old))select.value=old; else if(ordered.length)select.value=ordered[0]; } catch(e){}
  }
  function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  async function drilldown(dataIndex){
    const col=$('chartColumn').value; const value=chart?.data?.labels?.[dataIndex]; if(value===undefined)return;
    try{await request('/api/drilldown',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({column:col,value})});$('filterStatus').textContent=`Drilled into ${col.replaceAll('_',' ')}: ${value}`;$('filterStatus').className='filter-status';window.dispatchEvent(new Event('dataset:filtered'));await refresh();$('filters').scrollIntoView({behavior:'smooth',block:'center'});}catch(e){toast(e.message);}
  }
  async function drawChart(){
    try { const col=$('chartColumn').value; const type=$('chartType').value; const data=await request(`/chart/${encodeURIComponent(type)}/${encodeURIComponent(col)}`); if(chart)chart.destroy(); $('chartEmpty').style.display=data.labels.length?'none':'flex';
      chart=new Chart($('revenueChart'),{type:type==='line'?'line':type==='pie'?'doughnut':'bar',data:{labels:data.labels,datasets:[{label:data.metric,data:data.values,backgroundColor:type==='pie'?colors:colors[0],borderColor:type==='line'?colors[0]:undefined,borderRadius:type==='bar'?5:0,borderWidth:type==='line'?2:0,tension:.35,fill:type==='line'?{target:'origin',above:'rgba(63,131,237,.08)'}:false,pointRadius:type==='line'?3:0}]},options:{responsive:true,maintainAspectRatio:false,onClick:(_event,elements)=>{if(elements.length&&type!=='line')drilldown(elements[0].index);},plugins:{legend:{display:type==='pie',position:'bottom',labels:{boxWidth:8,boxHeight:8,usePointStyle:true,font:{family:'DM Sans',size:10},color:'#78869a'}},tooltip:{callbacks:{label:(ctx)=>`${ctx.label}: ${formatNumber(ctx.raw,true)}`}}},scales:type==='pie'?{}:{x:{grid:{display:false},ticks:{color:'#8995a6',font:{family:'DM Sans',size:9}},border:{display:false}},y:{beginAtZero:true,grid:{color:'#edf0f5'},ticks:{color:'#8995a6',font:{family:'DM Sans',size:9},callback:(v)=>formatNumber(v)},border:{display:false}}}}});
    } catch(e){$('chartEmpty').style.display='flex';$('chartEmpty').innerHTML=`<strong>${escapeHtml(e.message)}</strong>`;}
  }
  async function loadPreview(){
    try {const lim=$('previewLimit').value;const d=await request(`/api/preview?limit=${lim}`);$('rowCount').textContent=`${formatNumber(d.total)} rows`;
      if(!d.rows.length){$('tableContainer').innerHTML='<div class="table-empty"><strong>No rows match the current filters</strong></div>';return;}
      const head=d.columns.map(c=>`<th>${escapeHtml(c.replaceAll('_',' '))}</th>`).join(''); const body=d.rows.map(row=>`<tr>${d.columns.map(c=>`<td>${escapeHtml(row[c]===null||row[c]===undefined?'—':typeof row[c]==='object'?JSON.stringify(row[c]):row[c])}</td>`).join('')}</tr>`).join('');
      $('tableContainer').innerHTML=`<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
      const numeric=d.columns.filter(c=>d.rows.some(r=>typeof r[c]==='number')); const missing=d.columns.reduce((acc,c)=>acc+d.rows.filter(r=>r[c]===null||r[c]==='').length,0); $('healthBody').innerHTML=`<ul class="health-list"><li><span>Rows in current view</span><strong>${formatNumber(d.total)}</strong></li><li><span>Available fields</span><strong>${d.columns.length}</strong></li><li><span>Numeric fields</span><strong>${numeric.length}</strong></li><li><span>Missing cells (preview)</span><strong>${formatNumber(missing)}</strong></li></ul>`;
    }catch(e){$('tableContainer').innerHTML=`<div class="table-empty"><strong>${escapeHtml(e.message)}</strong></div>`;}
  }
  async function refresh(){try{const s=await refreshStatus();if(s.loaded){await Promise.all([loadKpis(),loadColumns(),loadPreview()]);await drawChart();}else{$('healthBody').innerHTML='<div class="health-empty">Load a dataset to see its profile.</div>';$('tableContainer').innerHTML='<div class="table-empty"><div class="empty-icon">▤</div><strong>No dataset yet</strong><span>Load a CSV or Excel file to see a preview.</span></div>';if(chart){chart.destroy();chart=null;}}window.dispatchEvent(new Event('dataset:changed'));}catch(e){toast(e.message);}}
  async function uploadFiles(){const input=$('fileInput');if(!input.files.length){setStatus('uploadStatus','Select at least one file first.','error');return;}const form=new FormData();for(const f of input.files)form.append('files',f);form.append('mode',$('uploadMode').value);setStatus('uploadStatus','Loading and preparing your data…');$('uploadButton').disabled=true;try{const data=await request('/upload',{method:'POST',body:form});setStatus('uploadStatus',`${data.message} ${formatNumber(data.rows)} rows · ${data.columns.length} columns.`,'success');input.value='';await refresh();toast('Dataset loaded successfully');if(data.errors?.length)toast(data.errors.join(' · '));}catch(e){setStatus('uploadStatus',e.message,'error');}finally{$('uploadButton').disabled=false;}}
  async function loadMySQL(){setStatus('mysqlStatus','Connecting…');$('mysqlButton').disabled=true;try{const data=await request('/load_mysql',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:$('mysqlQuery').value})});setStatus('mysqlStatus',`${data.message} ${formatNumber(data.rows)} rows.`,'success');await refresh();}catch(e){setStatus('mysqlStatus',e.message,'error');}finally{$('mysqlButton').disabled=false;}}
  async function reset(){if(!confirm('Clear your loaded dataset from this account?'))return;try{await request('/reset',{method:'POST'});await refresh();setStatus('uploadStatus','Dataset cleared.','success');toast('Dataset cleared');}catch(e){toast(e.message);}}

  window.refreshDataStudio=refresh;window.showDataStudioToast=toast;
  document.addEventListener('DOMContentLoaded',()=>{
    $('uploadButton').addEventListener('click',uploadFiles);$('mysqlButton').addEventListener('click',loadMySQL);$('resetButton').addEventListener('click',reset);$('refreshButton').addEventListener('click',refresh);$('chartColumn').addEventListener('change',drawChart);$('chartType').addEventListener('change',drawChart);$('previewLimit').addEventListener('change',loadPreview);
    $('mysqlToggle').addEventListener('click',()=>{$('mysqlPanel').scrollIntoView({behavior:'smooth',block:'center'});$('mysqlQuery').focus();});
    const zone=$('dropZone');zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('dragover')});zone.addEventListener('dragleave',()=>zone.classList.remove('dragover'));zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('dragover');if(e.dataTransfer.files.length){$('fileInput').files=e.dataTransfer.files;setStatus('uploadStatus',`${e.dataTransfer.files.length} file(s) selected.`);}});$('fileInput').addEventListener('change',e=>{if(e.target.files.length)setStatus('uploadStatus',`${e.target.files.length} file(s) selected.`);});
    document.querySelectorAll('.export-link').forEach(a=>a.addEventListener('click',e=>{if(a.getAttribute('aria-disabled')==='true'){e.preventDefault();toast('Load a dataset before exporting.');}}));
    refresh();
  });
})();
