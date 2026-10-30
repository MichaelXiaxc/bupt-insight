'use strict';
const $ = id => document.getElementById(id);
const state = {view:'recommended', category:'', q:'', source:'', sort:'recommended', page:1, version:null, item:null, request:0};
const views = {recommended:['今天，少看一点','只留近期未读、与你相关的机会；普通精选最多 8 条，重要教务另列。','为你推荐'], latest:['新收录与更新','按本地收录时间浏览；发布时间另行标注，旧公告不会变成新公告。','最新收录'], unread:['还没读过的信息','看过的内容会留在历史中；正文有变化时会重新标为未读。','未读信息'], favorites:['留给以后的好机会','先收藏，等有时间再认真看看。','我的收藏'], all:['校园信息，完整保留','不依赖推荐判断，在这里发现算法可能漏掉的信息。','全部历史'], hidden:['暂时不感兴趣','内容没有被删除，随时可以恢复。重要教务事项仍受保护。','不感兴趣']};
const icons = {research:'research',competition:'competition',internship:'internship',event:'event',mandatory_academic:'academic',general:'general',electronics:'electronics'};
views.research=['本科科研招募','近90天的科研合作、导师和实验室招募，按发布时间排序；研究内容、参与条件与申请方式以原文为准。','本科科研招募'];
function el(tag,cls,text){const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x;}
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');svg.setAttribute('class','ui-icon');svg.setAttribute('aria-hidden','true');use.setAttribute('href','/static/icons.svg#'+name);svg.append(use);return svg;}
function defaultSort(){return state.view==='recommended'?'recommended':state.view==='latest'?'collected':'published';}
function syncFilters(){const filtered=Boolean(state.q||state.category||state.source);$('reset-filters').hidden=!filtered&&state.sort===defaultSort();document.querySelectorAll('[data-category]').forEach(button=>{const active=button.dataset.category===state.category;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});}
function resetFilters(){clearTimeout(state.searchTimer);state.q='';state.category='';state.source='';state.sort=defaultSort();state.page=1;$('search').value='';$('source-filter').value='';$('sort').value=state.sort;loadFeed();}
function setDensity(density){const compact=density==='compact';$('feed').classList.toggle('compact',compact);for(const mode of ['comfortable','compact']){const button=$('density-'+mode),active=density===mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}try{localStorage.setItem('bupt-insight-density',density);}catch{}}
function dateText(value,withTime=false){if(!value)return '暂无成功记录';const d=new Date(value);if(Number.isNaN(d.getTime()))return value;return d.toLocaleString('zh-CN',withTime?{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}:{year:'numeric',month:'2-digit',day:'2-digit'});}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3000);}
async function api(path,body){const options=body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify(body)}:{};const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(data.message||'暂时无法读取，请稍后重试。');return data;}
async function mutate(item,action,value){await api('/api/state',{id:item.id,hash:item.hash,action,value});}
function renderCard(item){
 const card=el('article','card'+(item.unread?'':' read'));card.dataset.id=item.id;
 const categoryIcon=el('div','category-icon '+item.category);categoryIcon.append(icon(icons[item.category]||'general'));card.append(categoryIcon);
 const main=el('div','card-main'),meta=el('div','card-meta');
 meta.append(el('span','tag',item.category_label),el('span','','北邮人论坛'),el('span','','·'),el('span','',item.origin||'来源待核实'),el('span','','·'),el('time','',item.published||'发布时间未知'));
 if(item.unread&&item.revision>1)meta.append(el('span','tag new','正文有更新'));
 if(item.historical)meta.append(el('span','tag','较早历史 · 留意有效期'));
 if(item.importance==='high')meta.append(el('span','tag priority','重要事项'));
 if(item.research.candidate)meta.append(el('span','tag',item.research.undergraduate?'原文提及本科生':'参与条件见原文'));
 if(item.research.mentor)meta.append(el('span','tag','含指导团队介绍'));
 if(item.research.time_commitment)meta.append(el('span','tag','含时间投入要求'));
 if(item.hidden)meta.append(el('span','tag','已标不感兴趣'));
 main.append(meta);const h=el('h2'),title=el('button','title-button');
 if(item.unread)title.append(el('span','unread-dot'));title.append(document.createTextNode(item.title));title.addEventListener('click',()=>openReader(item.id));h.append(title);main.append(h);
 main.append(el('p','excerpt',item.summary));
 if(item.deadline.value)main.append(el('div','deadline','原文截止日期：'+item.deadline.value+' · 具体时刻请核对原文'));
 const bottom=el('div','card-bottom');const reason=item.reasons.length?item.reasons[0].text:'正文摘录 · 适用条件与有效期请查看原文';
 bottom.append(el('div','recommend-reason',(item.relevance==='high'?'✦ ':'')+reason));const actions=el('div','card-actions');
 const save=el('button','card-action'+(item.favorite?' saved':''));save.append(icon('star'));save.title=item.favorite?'取消收藏':'收藏';save.setAttribute('aria-label',save.title);save.setAttribute('aria-pressed',String(item.favorite));save.addEventListener('click',async()=>{save.disabled=true;try{await mutate(item,'favorite',!item.favorite);await loadFeed();toast(item.favorite?'已取消收藏':'已收藏，稍后再看');}catch(e){toast(e.message);}finally{save.disabled=false;}});
 const hide=el('button','card-action');hide.append(icon(item.hidden?'undo':'hide'));hide.title=item.importance==='high'?'重要教务事项不可隐藏':item.hidden?'恢复到信息流':'不感兴趣';hide.setAttribute('aria-label',hide.title);hide.disabled=item.importance==='high';hide.addEventListener('click',async()=>{hide.disabled=true;try{await mutate(item,'hidden',!item.hidden);await loadFeed();toast(item.hidden?'已恢复，内容仍保留在历史中':'已收起，可在“不感兴趣”中恢复');}catch(e){toast(e.message);}finally{hide.disabled=item.importance==='high';}});
 actions.append(save,hide);bottom.append(actions);main.append(bottom);card.append(main);return card;
}
async function loadFeed(){
 const seq=++state.request;$('error-banner').hidden=true;syncFilters();$('feed').setAttribute('aria-busy','true');$('refresh').disabled=true;$('prev-page').disabled=true;$('next-page').disabled=true;
 const params=new URLSearchParams({view:state.view,category:state.category,q:state.q,source:state.source,sort:state.sort,page:state.page});
 try{const data=await api('/api/feed?'+params);if(seq!==state.request)return;
  state.version=data.version;$('new-banner').hidden=true;$('feed').replaceChildren();
  if(data.items.length)data.items.forEach(item=>$('feed').append(renderCard(item)));else{const empty=el('div','empty'),filtered=Boolean(state.q||state.category||state.source);const copy=filtered?['没有找到匹配的信息','试试更简短的关键词，或清除筛选重新发现。']:state.view==='favorites'?['给好机会留个位置','点击信息卡片上的星标，就能在这里找到收藏。']:state.view==='hidden'?['这里空空的，也很好','标记为“不感兴趣”的内容会收在这里，随时可以恢复。']:state.view==='unread'?['这一刻，都读过了','新的收录与正文更新会再次提醒你。']:state.view==='recommended'?['今天的空白，留给自己','暂时没有新的精选。也可以去全部历史，自由发现。']:['这里暂时没有信息','新内容收录后会出现在这里，请留意右侧的实际采集状态。'];empty.append(el('strong','',copy[0]),el('p','',copy[1]));if(filtered||state.view==='recommended'){const button=el('button','secondary-button',filtered?'清除筛选':'浏览全部历史');button.onclick=filtered?resetFilters:()=>switchView('all');empty.append(button);}$('feed').append(empty);}
  $('result-count').textContent=data.total;$('nav-unread').textContent=data.counts.unread;$('nav-favorites').textContent=data.counts.favorites||'';for(const key of ['unread','favorites','all'])$('overview-'+key).textContent=Number(data.counts[key]||0).toLocaleString('zh-CN');
  const pages=Math.max(1,Math.ceil(data.total/data.page_size));$('pagination-label').textContent=`第 ${data.page} / ${pages} 页`;$('prev-page').disabled=data.page<=1;$('next-page').disabled=data.page>=pages;
 }catch(e){if(seq!==state.request)return;$('error-banner').textContent=e.message;$('error-banner').hidden=false;const empty=el('div','empty'),retry=el('button','secondary-button','重新加载');retry.onclick=loadFeed;empty.append(el('strong','','暂时无法打开本地数据'),el('p','','采集和历史文件不会因此被清空，请稍后重试。'),retry);$('feed').replaceChildren(empty);$('result-count').textContent='—';$('pagination-label').textContent='加载未完成';}
 finally{if(seq===state.request){$('feed').setAttribute('aria-busy','false');$('refresh').disabled=false;}}
}
function switchView(view){if(!Object.hasOwn(views,view))return;clearTimeout(state.searchTimer);state.q=$('search').value;state.view=view;state.page=1;state.sort=defaultSort();$('sort').value=state.sort;const copy=views[view],spark=el('span','title-spark','✦');spark.setAttribute('aria-hidden','true');$('view-title').replaceChildren(document.createTextNode(copy[0]),spark);$('view-subtitle').textContent=copy[1];$('breadcrumb-view').textContent=copy[2];$('list-context').textContent=view==='recommended'?'近 30 天 · 未读精选':view==='research'?'近 90 天 · 招募有效期请核对原文':'按所选顺序浏览 · 历史完整保留';document.querySelectorAll('[data-view]').forEach(button=>{const active=button.dataset.view===view;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});loadFeed();}
let readerRequest=0,readerTrigger=null;
async function openReader(id){
 const request=++readerRequest;state.item=null;if(!$('reader-drawer').contains(document.activeElement))readerTrigger=document.activeElement;$('reader-drawer').setAttribute('aria-busy','true');$('reader-drawer').hidden=false;$('drawer-backdrop').hidden=false;document.body.style.overflow='hidden';document.querySelector('.sidebar').inert=true;document.querySelector('.workspace').inert=true;const loading=el('div','loading','正在打开正文…');loading.id='detail-heading';$('reader-content').replaceChildren(loading);$('reader-drawer').scrollTop=0;$('close-reader').focus();
 try{const item=await api('/api/detail?'+new URLSearchParams({id}));if(request!==readerRequest)return;state.item=item;$('reader-content').replaceChildren();const box=$('reader-content'),heading=el('h2','detail-title',item.title);heading.id='detail-heading';
  box.append(el('span','tag',item.category_label),heading,el('div','detail-meta',`北邮人论坛 · ${item.origin} · 发布于 ${item.published||'未知'}`));
  const controls=el('div','detail-controls');if(item.url){const a=el('a','original-link','打开原文 ↗');a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';controls.append(a);}
  const favorite=el('button','secondary-button',item.favorite?'★ 已收藏':'☆ 收藏');favorite.onclick=async()=>{try{await mutate(item,'favorite',!item.favorite);item.favorite=!item.favorite;favorite.textContent=item.favorite?'★ 已收藏':'☆ 收藏';loadFeed();}catch(e){toast(e.message);}};
  const unread=el('button','secondary-button','标为未读');unread.onclick=async()=>{try{await mutate(item,'read',false);toast('已标为未读');closeReader();loadFeed();}catch(e){toast(e.message);}};controls.append(favorite,unread);box.append(controls);
  const insight=el('section','insight-box');insight.append(el('strong','','主题与适用性 · 本地规则判断'));
  if(!item.reasons.length)insight.append(el('p','','目前没有足够信息判断个人相关性。保留原文供你自行发现机会。'));
  item.reasons.forEach(r=>{insight.append(el('p','','推断：'+r.text));if(r.evidence)insight.append(el('div','evidence','原文证据：'+r.evidence));});
  if(item.deadline.value){insight.append(el('p','','原文明确的截止日期：'+item.deadline.value),el('div','evidence',item.deadline.evidence));}else insight.append(el('p','','截止时间：无法确认。没有完整年份或存在多处截止时，不推测日期。'));
  insight.append(el('p','','行动建议：核对适用对象、报名方式与当前有效期；这里不代表你已经满足资格。'));box.append(insight);
  if(item.body_status!=='ok')box.append(el('div','error-banner',item.body_status==='image_only'?'本通知正文为图片，文字尚未提取，请打开原文查看。':item.body_status==='attachment_only'?'本通知以附件提供正文，附件内容尚未提取。':'正文采集不完整，请以原文为准。'));
  if(item.research.candidate){
   box.append(el('div','body-label','科研招募信息 · 原文摘录'));
   [['招募说明',item.research.recruitment],['参与条件',item.research.undergraduate],['项目安排',item.research.ongoing],['指导团队',item.research.mentor],['时间投入',item.research.time_commitment],['合作说明',item.research.authorship]].forEach(([label,evidence])=>{if(evidence)box.append(el('p','research-evidence',label+'：'+evidence));});
   if(item.research.deadline)box.append(el('p','deadline','招募截止：'+item.research.deadline+(item.research.deadline_inferred?'（年份据发帖日期推定，请核对）':'')));
  }
  box.append(el('div','body-label','收录正文 · 以原通知为准'),el('div','detail-body',item.body));
  item.attachments.forEach(a=>{if(a.url){const link=el('a','original-link',a.name+' ↗');link.href=a.url;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}});
  box.append(el('div','detail-foot',`本地首次收录：${dateText(item.first_seen,true)} · 当前版本 ${item.revision}。发布时间与收录时间不同。`));
  const feedback=el('div','feedback-buttons');[['relevant','与我相关'],['important','很重要'],['irrelevant','不感兴趣'],['unsure','暂不确定']].forEach(([value,label])=>{const b=el('button',item.feedback===value?'active':'',label);b.onclick=async()=>{try{await mutate(item,'feedback',value);feedback.querySelectorAll('button').forEach(x=>x.classList.remove('active'));b.classList.add('active');toast('反馈已本地保存；采集忙时会稍后同步');loadFeed();}catch(e){toast(e.message);}};feedback.append(b);});box.append(feedback);
  $('reader-drawer').setAttribute('aria-busy','false');
  await mutate(item,'read',true);loadFeed();
 }catch(e){if(request!==readerRequest)return;$('reader-drawer').setAttribute('aria-busy','false');if(!state.item){const heading=el('h2','detail-title','正文暂时无法打开'),retry=el('button','secondary-button','重新加载');heading.id='detail-heading';retry.onclick=()=>openReader(id);$('reader-content').replaceChildren(heading,el('p','detail-meta',e.message),retry);}else toast(e.message);}
}
function closeReader(){if($('reader-drawer').hidden)return;readerRequest++;$('reader-drawer').hidden=true;$('drawer-backdrop').hidden=true;document.body.style.overflow='';document.querySelector('.sidebar').inert=false;document.querySelector('.workspace').inert=false;state.item=null;if(readerTrigger?.isConnected&&readerTrigger.getClientRects().length&&!$('reader-drawer').contains(readerTrigger))readerTrigger.focus();else document.querySelector('[data-view].active').focus();}
let latestStatus=null;
async function checkStatus(poll=false){try{const data=await api('/api/status');latestStatus=data;const labels={ok:'已更新',no_new:'无新增',running:'正在采集',stuck:'采集未完成',stale:'较久未成功',never:'尚未采集',login_expired:'需要重新登录',failed:'采集失败',partial_failed:'部分采集失败',interrupted:'上次采集中断'};for(const key of ['byr']){const source=data.sources[key],status=$(key+'-status');status.replaceChildren(el('strong','',labels[source.status]||'需要检查'),el('span','',source.last_success?'上次成功 '+dateText(source.last_success,true):'暂无成功记录'));status.classList.toggle('failed',!['ok','no_new','running'].includes(source.status));}
 if(poll&&state.version){const feed=await api('/api/feed?view=all');if(feed.version!==state.version)$('new-banner').hidden=false;}
 }catch(e){for(const key of ['byr']){$(key+'-status').textContent='状态暂不可用';$(key+'-status').classList.add('failed');}}}
function info(title,paragraphs){$('info-title').textContent=title;$('info-content').replaceChildren(...paragraphs.map(x=>el('p','',x)));$('info-dialog').showModal();}
$('navigation').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(b)switchView(b.dataset.view);});
$('topics').addEventListener('click',e=>{const b=e.target.closest('[data-category]');if(!b)return;state.category=b.dataset.category;state.page=1;loadFeed();});
document.querySelectorAll('[data-open-view]').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.openView)));
$('reset-filters').onclick=resetFilters;for(const density of ['comfortable','compact'])$('density-'+density).onclick=()=>setDensity(density);try{setDensity(localStorage.getItem('bupt-insight-density')==='compact'?'compact':'comfortable');}catch{setDensity('comfortable');}
$('search').addEventListener('input',()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(()=>{state.q=$('search').value;state.page=1;loadFeed();},220);});
$('source-filter').onchange=()=>{state.source=$('source-filter').value;state.page=1;loadFeed();};$('sort').onchange=()=>{state.sort=$('sort').value;state.page=1;loadFeed();};
$('refresh').onclick=()=>{loadFeed();checkStatus();};$('load-new').onclick=()=>{state.page=1;loadFeed();checkStatus();};$('prev-page').onclick=()=>{state.page--;loadFeed();window.scrollTo({top:150,behavior:'smooth'});};$('next-page').onclick=()=>{state.page++;loadFeed();window.scrollTo({top:150,behavior:'smooth'});};
$('close-reader').onclick=closeReader;$('drawer-backdrop').onclick=closeReader;$('close-info').onclick=()=>$('info-dialog').close();
$('help-button').onclick=()=>info('每天看五分钟，就够了',['首页只保留近30天未读且有明确推荐依据的机会，普通最多8条；已读、过期及较早历史不再占据首页。重要教务另列。收藏会保存到本机；关闭网页也不会丢失。','读过的信息留在完整历史中。正文发生变化会重新标为未读。不感兴趣的内容可以随时恢复，重要教务事项受保护。','采集由服务器上的定时任务独立执行。服务器关机、休眠或校园网/VPN 断开时可能无法更新，请留意上方的最后成功时间。','学校原文按纯文本展示，不加载外部图片、脚本或字体。网页只在本机开放，不调用云端模型。关闭浏览器不会停止采集；用“关闭 BUPT Insight”快捷方式停止阅读服务。']);
$('status-details').onclick=()=>info('信息从哪里来',latestStatus?['最近成功时间代表实际业务采集成功，不是网页刷新时间。无新增仍属于一次成功检查。',...Object.entries(latestStatus.sources).map(([k,s])=>`北邮人论坛：${s.status}；最后成功 ${dateText(s.last_success,true)}；最近一轮读取 ${s.total} 条。`),latestStatus.feedback_pending?`还有 ${latestStatus.feedback_pending} 条反馈等待采集结束后同步，已经安全保存在本机。`:'反馈同步正常。']:['暂时无法取得运行状态，请稍后刷新。']);
document.addEventListener('keydown',event=>{if($('info-dialog').open)return;if(!$('reader-drawer').hidden){if(event.key==='Escape')closeReader();if(event.key==='Tab'){const controls=Array.from($('reader-drawer').querySelectorAll('button:not(:disabled),a[href],[tabindex="0"]')).filter(control=>control.getClientRects().length),first=controls[0],last=controls[controls.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}return;}if(event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!document.activeElement.isContentEditable&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){event.preventDefault();$('search').focus();}});
const staticIcons=[['[data-view="recommended"] .nav-symbol','spark'],['[data-view="research"] .nav-symbol','research'],['[data-view="latest"] .nav-symbol','clock'],['[data-view="unread"] .nav-symbol','unread'],['[data-view="favorites"] .nav-symbol','star'],['[data-view="all"] .nav-symbol','archive'],['[data-view="hidden"] .nav-symbol','hide'],['.privacy-icon','shield'],['.today','clock'],['#refresh','refresh'],['.overview-icon:not(.apricot):not(.lavender)','unread'],['.overview-icon.apricot','star'],['.overview-icon.lavender','archive'],['.search-box>span','search'],['#density-comfortable','comfortable'],['#density-compact','compact'],['.page-note>span:first-child','leaf']];
for(const [selector,name] of staticIcons){const target=document.querySelector(selector),label=target.querySelector('span');if(selector==='.today'||selector==='#refresh'){target.replaceChildren(icon(name),label);}else target.replaceChildren(icon(name));}
$('today-label').textContent=new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});const initialView=new URLSearchParams(window.location.search).get('view');if(Object.hasOwn(views,initialView))switchView(initialView);else loadFeed();checkStatus();setInterval(()=>checkStatus(true),30000);
