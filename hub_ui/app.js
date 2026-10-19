'use strict';
let csrf = '', department='all', role='all', items=[], cursor=0, stream;
const $ = s => document.querySelector(s);
const labels = Object.assign(Object.create(null),{all:'すべてのメッセージ',general:'全体連絡'});
let departmentsKey;
function el(tag, className, text){const n=document.createElement(tag); if(className)n.className=className;if(text!==undefined)n.textContent=text;return n;}
async function loadDepartments(){const data=await getJSON('/api/state');const key=JSON.stringify(data.departments);if(key===departmentsKey)return;
 const root=$('#departments'),select=$('#mobile-dept');root.replaceChildren();select.replaceChildren();
 for(const d of ['all','general',...data.departments]){labels[d]=labels[d]||d;const option=el('option','',labels[d]);option.value=d;select.append(option);}
 if(data.departments.length)root.append(el('div','heading','PROJECT CHANNELS'));
 for(const d of data.departments){const button=el('button','nav-item',labels[d]);button.dataset.dept=d;button.classList.toggle('current',d===department);button.addEventListener('click',()=>setDepartment(d));root.append(button);}
 select.value=department;departmentsKey=key;
}
function setDepartment(d){ department=d;cursor=0;items=[];$('#title').textContent=labels[d];$('#mobile-dept').value=d;
  document.querySelectorAll('[data-dept]').forEach(x=>x.classList.toggle('current',x.dataset.dept===d)); loadMessages(); }
function render(){const root=$('#messages');root.replaceChildren();const q=$('#search').value.toLocaleLowerCase();
 const visible=items.filter(m=>(role==='all'||m.role===role)&&(!q||m.body.toLocaleLowerCase().includes(q)||m.author_id.toLocaleLowerCase().includes(q)));
 if(!visible.length){root.append(el('div','empty',items.length?'検索条件に合うメッセージはありません':'まだメッセージはありません。実際の会話と観測記録のみ表示されます。'));return;}
 for(const m of visible){const card=el('article','message '+m.role),av=el('div','avatar',m.role==='sol'?'SOL':m.role==='astra'?'AST':m.role==='operator'?'YOU':'SYS'),content=el('div','message-content'),meta=el('div','message-meta');
 meta.append(el('strong','',m.author_id),el('span','',m.department+' · '+new Date(m.created_at).toLocaleString('ja-JP')),el('span','',m.provenance));
 content.append(meta,el('div','message-body',m.body));card.append(av,content);root.append(card);}
 root.scrollTop=root.scrollHeight;
}
async function getJSON(path){const r=await fetch(path,{credentials:'same-origin'});if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}
async function loadMessages(){const d=department;const after=cursor;try{const j=await getJSON('/api/messages?department='+encodeURIComponent(d)+'&after='+after);
 if(department!==d)return;for(const m of j.messages){if(!items.some(x=>x.id===m.id))items.push(m);cursor=Math.max(cursor,m.source_seq);}render();}
 catch(e){$('#post-state').textContent='接続待機: '+e.message;}}
async function loadCheckpoints(){try{const data=await getJSON('/api/checkpoints');const root=$('#checkpoints');root.replaceChildren();
 if(!data.checkpoints.length){root.append(el('p','muted','記録されたチェックポイントはありません'));return;}
 for(const ck of data.checkpoints.slice(0,12)){const row=el('div','checkpoint');row.append(el('strong','',ck.task+' · '+ck.status),el('span','',ck.agent+' · '+new Date(ck.created_at).toLocaleString('ja-JP')));root.append(row);}}
 catch(_){/* UI may remain available during interrupted writes. */}}
async function init(){try{const data=await getJSON('/api/session');csrf=data.csrf;$('#service-status').textContent=data.chat_enabled?'プロジェクトチャット ON':'プロジェクトチャット OFF';$('#mode-dot').classList.toggle('ok',data.chat_enabled);
 $('#project-id').textContent=data.project_id;$('#project-short').textContent=data.project_id.substring(0,14)+'…';$('#submit').disabled=!data.chat_enabled;
 await loadDepartments();await loadMessages();await loadCheckpoints();startStream();}catch(e){$('#messages').replaceChildren(el('div','empty','認証または通信に失敗しました。サーバーの表示するログインURLから開き直してください。'));}}
function refresh(){loadDepartments().catch(e=>{$('#post-state').textContent='接続待機: '+e.message;});loadMessages();loadCheckpoints();}
function startStream(){if(!window.EventSource){setInterval(refresh,3500);return;}
 stream=new EventSource('/api/stream?after='+cursor);stream.onmessage=refresh;stream.onerror=()=>{setTimeout(refresh,1500);};}
document.querySelectorAll('[data-dept]').forEach(x=>x.addEventListener('click',()=>setDepartment(x.dataset.dept)));
$('#mobile-dept').addEventListener('change',e=>setDepartment(e.target.value));
document.querySelectorAll('.filter').forEach(x=>x.addEventListener('click',()=>{role=x.dataset.role;document.querySelectorAll('.filter').forEach(z=>z.classList.toggle('selected',z===x));render();}));
$('#search').addEventListener('input',render);
$('#theme').addEventListener('click',()=>document.body.classList.toggle('light'));
$('#compose').addEventListener('submit',async e=>{e.preventDefault();const body=$('#body').value.trim();if(!body)return;
 if(department==='all'){ $('#post-state').textContent='投稿先または全体連絡を選択してください';return; }
 try{const r=await fetch('/api/operator-message',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CCG-CSRF':csrf},body:JSON.stringify({department,body})});
 const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);$('#body').value='';$('#post-state').textContent='保存済み（Codexへの自動配送は未接続）';await loadMessages();}
 catch(err){$('#post-state').textContent='投稿失敗: '+err.message;}});
init();
