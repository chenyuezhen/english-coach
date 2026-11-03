const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const todayStr = () => { const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const fmtD = d => d ? d.slice(5).replace('-','/') : '';
const TYPE = {vocab:'词汇',collocation:'搭配',register:'语域',grammar:'句法',scenario:'场景'};

let TOKEN = localStorage.getItem('ec_token') || '';
let ME = null;
let calCursor = new Date();
let selDay = todayStr();
let curPaper = null;

function toast(m){
  const t=$('#toast'); t.textContent=m; t.classList.add('on');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('on'),2000);
}

function confetti(){
  const box=$('#confetti'); const colors=['#c15f3c','#4a7a5c','#7a6a94','#a8762f','#d9d4c7'];
  for(let i=0;i<26;i++){
    const s=document.createElement('div'); s.className='conf';
    s.style.left=(Math.random()*100)+'%';
    s.style.background=colors[i%colors.length];
    s.style.animationDelay=(Math.random()*.28)+'s';
    s.style.width=s.style.height=(5+Math.random()*7)+'px';
    box.appendChild(s);
    setTimeout(()=>s.remove(),2000);
  }
}

async function api(path, opts={}){
  const o={...opts, headers:{...(opts.headers||{})}};
  if(TOKEN) o.headers['Authorization']='Bearer '+TOKEN;
  if(o.body && typeof o.body!=='string'){ o.headers['Content-Type']='application/json'; o.body=JSON.stringify(o.body); }
  const r=await fetch(path,o);
  const d=await r.json().catch(()=>({error:'响应解析失败'}));
  if(r.status===401){
    const had=!!TOKEN; logout(true);
    if(had) toast('登录已失效，请重新登录');
    throw new Error('登录已失效');
  }
  if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
  return d;
}

async function login(){
  const btn=$('#loginBtn'), err=$('#loginErr');
  err.classList.remove('show');
  btn.disabled=true; btn.innerHTML='<span class="spin"></span> 进入中';
  try{
    const d=await api('/api/login',{method:'POST',body:{username:$('#u').value.trim(),password:$('#p').value}});
    TOKEN=d.token; localStorage.setItem('ec_token',TOKEN); ME=d.user; boot();
  }catch(e){ err.textContent=e.message; err.classList.add('show'); }
  finally{ btn.disabled=false; btn.textContent='进入'; }
}

function logout(silent){
  if(!silent && TOKEN){
    const t=TOKEN; TOKEN=''; localStorage.removeItem('ec_token');
    fetch('/api/logout',{method:'POST',headers:{'Authorization':'Bearer '+t}}).catch(()=>{});
    ME=null; $('#app').style.display='none'; $('#login').style.display='flex'; return;
  }
  TOKEN=''; ME=null; localStorage.removeItem('ec_token');
  $('#app').style.display='none'; $('#login').style.display='flex';
}

$('#loginBtn').onclick=login;
$('#p').addEventListener('keydown',e=>{ if(e.key==='Enter') login(); });
$('#logoutBtn').onclick=()=>logout();
document.querySelectorAll('.nav[data-view]').forEach(b=>{ b.onclick=()=>showView(b.dataset.view); });

function showView(v){
  document.querySelectorAll('.nav[data-view]').forEach(b=>b.classList.toggle('on',b.dataset.view===v));
  document.querySelectorAll('.view').forEach(s=>s.classList.toggle('on',s.id==='v-'+v));
  if(v==='dash') loadDash();
  if(v==='reports') loadReports();
  if(v==='exam') loadExam();
  if(v==='plan') loadPlan();
  if(v==='stats') loadStats();
  if(v==='users') loadUsers();
}

function renderEvent(e){
  const lv=e.levels||{};
  const rows=[['平级','peer'],['对上级','manager'],['正式','formal_email']]
    .filter(([,k])=>lv[k]).map(([l,k])=>`<div class="lvr"><div class="lvk">${l}</div><div class="lvv">${esc(lv[k])}</div></div>`).join('');
  const sev = e.severity>=3?'<span class="tag coral">失礼</span>':e.severity==2?'<span class="tag">会读着别扭</span>':'<span class="tag">小别扭</span>';
  return `<div class="ev">
    <div class="ev-h">
      <span>${esc((e.ts||'').replace('T',' ').slice(0,16))}</span>
      ${e.app?`<span class="tag">${esc(e.app)}</span>`:''}
      <span class="tag lime">${TYPE[e.type]||e.type||'—'}</span>
      <span style="color:var(--text-2);font-weight:560;font-size:12px">${esc(e.topic||'')}</span>
      ${sev}
    </div>
    ${e.selected?`<div class="src">${esc(e.selected)}</div>`:''}
    ${e.intent_zh?`<div class="why">想说的是：${esc(e.intent_zh)}</div>`:''}
    ${e.why_wrong?`<div class="why">${esc(e.why_wrong)}</div>`:''}
    ${rows?`<div class="lvs">${rows}</div>`:''}
    ${e.pattern?`<div class="pat"><b>句型</b>${esc(e.pattern)}</div>`:''}
  </div>`;
}

async function loadDash(){
  const h=new Date().getHours();
  $('#dashGreet').textContent = h<5?'还没睡':h<11?'早上好':h<14?'中午好':h<18?'下午好':'晚上好';
  $('#dashSub').textContent = new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});
  try{
    const [st,evs,ex]=await Promise.all([
      api('/api/stats?weeks=1'), api('/api/events?limit=4'), api('/api/exam/today').catch(()=>({}))
    ]);
    const t=st.totals;
    $('#hero').innerHTML=`
      <div class="lvl">
        <div class="lvl-k">Level ${t.level}</div>
        <div class="lvl-n"><b>${t.xp}</b><span>XP</span></div>
        <div class="lvl-bar"><i style="width:${Math.min(100,t.level_xp/t.level_next*100)}%"></i></div>
        <div class="lvl-x">${t.level_xp} / ${t.level_next} 到下一级</div>
      </div>
      <div class="mt"><div class="mt-k">本周记录</div><div class="mt-v">${t.reports}<small>条</small></div><div class="mt-d">合计</div></div>
      <div class="mt"><div class="mt-k">连续</div><div class="mt-v">${t.streak}<small>天</small></div><div class="mt-d">连续有记录</div></div>
      <div class="mt"><div class="mt-k">正确率</div><div class="mt-v">${t.accuracy==null?'—':t.accuracy}${t.accuracy==null?'':'<small>%</small>'}</div><div class="mt-d">${t.mastered}/${t.cards} 已熟练</div></div>`;

    const q=$('#questBox');
    if(ex.empty){
      q.innerHTML=`<div class="quest"><div class="quest-top"><span class="qpill">今日练习</span></div>
        <h3 class="quest-n">没有可出的题</h3>
        <p class="quest-p">还没有记录。先记一条，就能出题。</p>
        <button class="btn btn-lime btn-block" onclick="openReport()">记第一笔</button></div>`;
    }else if(ex.submitted){
      const pct=ex.total?Math.round(ex.score/ex.total*100):0;
      const stars=pct>=100?3:pct>=60?2:pct>=30?1:0;
      q.innerHTML=`<div class="quest">
        <div class="quest-top"><span class="qpill done">已完成</span></div>
        <div class="quest-score">${ex.score}<small>/ ${ex.total}</small></div>
        <div class="stars">${[0,1,2].map(i=>`<i class="${i<stars?'on':''}">★</i>`).join('')}</div>
        <p class="quest-p" style="margin:14px 0 0">${stars===3?'全对。':stars===2?'还有几处要再看。':'答错的已降级，之后会重考。'}</p>
        <button class="btn btn-block" style="margin-top:12px" onclick="showView('exam')">看错题</button></div>`;
    }else{
      q.innerHTML=`<div class="quest">
        <div class="quest-top"><span class="qpill">今日练习</span></div>
        <h3 class="quest-n">${ex.questions.length} 道题</h3>
        <p class="quest-p">题目来自你的记录。答错的知识点会降级，之后还会出现。</p>
        <button class="btn btn-lime btn-block" onclick="showView('exam')">开始 · 约 2 分钟</button></div>`;
    }

    $('#dashRecent').innerHTML = evs.events.length ? evs.events.map(renderEvent).join('')
      : '<div class="empty"><span class="ic">✎</span>还没有记录</div>';
  }catch(e){ toast(e.message); }
  loadCalendar();
}

async function loadCalendar(){
  const y=calCursor.getFullYear(), m=calCursor.getMonth();
  const month=`${y}-${String(m+1).padStart(2,'0')}`;
  $('#calMonth').textContent=`${y} 年 ${m+1} 月`;
  let data={days:{},exams:{}};
  try{ data=await api('/api/days?month='+month); }catch(e){}
  const lead=(new Date(y,m,1).getDay()+6)%7;
  const total=new Date(y,m+1,0).getDate();
  const today=todayStr();
  let h=['一','二','三','四','五','六','日'].map(d=>`<div class="cd">${d}</div>`).join('');
  for(let i=0;i<lead;i++) h+='<div class="cc empty"></div>';
  for(let d=1;d<=total;d++){
    const ds=`${month}-${String(d).padStart(2,'0')}`;
    const has=data.days[ds]>0;
    const ex=data.exams[ds]&&data.exams[ds].score!=null;
    const cls=['cc',has?'has':'',ds===today?'today':'',ds===selDay?'sel':''].filter(Boolean).join(' ');
    h+=`<div class="${cls}" data-day="${ds}">${d}${ex?'<span class="ex"></span>':''}${has?'<span class="dt"></span>':''}</div>`;
  }
  $('#calGrid').innerHTML=h;
  document.querySelectorAll('.cc[data-day]').forEach(c=>{
    c.onclick=()=>{ selDay=c.dataset.day; $('#repDate').value=selDay; showView('reports'); };
  });
}
$('#calPrev').onclick=()=>{ calCursor.setMonth(calCursor.getMonth()-1); loadCalendar(); };
$('#calNext').onclick=()=>{ calCursor.setMonth(calCursor.getMonth()+1); loadCalendar(); };

async function loadReports(){
  if(!$('#repDate').value) $('#repDate').value=selDay;
  selDay=$('#repDate').value;
  $('#repSub').textContent=selDay;
  $('#repList').innerHTML='<div class="card pad"><div class="empty">读取中…</div></div>';
  try{
    const d=await api('/api/events?date='+selDay);
    $('#repList').innerHTML=d.events.length
      ? `<div class="card pad">${d.events.map(renderEvent).join('')}</div>`
      : `<div class="card pad"><div class="empty"><span class="ic">◇</span>这天没有记录</div></div>`;
  }catch(e){ toast(e.message); }
}
$('#repDate').onchange=loadReports;

function openReport(){ $('#mReport').classList.add('on'); $('#rSel').focus(); }
window.openReport = openReport;
$('#quickReport').onclick=openReport;
$('#repAdd').onclick=openReport;
$('#rCancel').onclick=()=>$('#mReport').classList.remove('on');
$('#rSubmit').onclick=async()=>{
  const sel=$('#rSel').value.trim(), intent=$('#rIntent').value.trim();
  if(!sel&&!intent) return toast('至少填一项');
  const btn=$('#rSubmit'); btn.disabled=true; btn.innerHTML='<span class="spin"></span> 分析中';
  try{
    const r=await api('/api/report',{method:'POST',body:{selected:sel,intent_zh:intent,app:$('#rApp').value.trim()}});
    $('#mReport').classList.remove('on');
    $('#rSel').value=''; $('#rIntent').value=''; $('#rApp').value='';
    toast('记下了 · '+(r.topic||''));
    $('#v-reports').classList.contains('on')?loadReports():loadDash();
  }catch(e){ toast('失败：'+e.message); }
  finally{ btn.disabled=false; btn.textContent='提交'; }
};

/* ====== 关卡 ====== */
async function loadExam(){
  $('#examCard').innerHTML='<div class="pad"><div class="empty">读取中…</div></div>';
  try{
    const d=await api('/api/exam/today');
    curPaper=d;
    if(d.empty){
      $('#examCard').innerHTML=`<div class="pad"><div class="empty"><span class="ic">◇</span>${esc(d.message||'暂无内容')}</div></div>`;
    }else{
      renderExam(d);
    }
    const h=await api('/api/exam/history');
    $('#examHist').innerHTML=h.papers.length?`<table class="t">
      <thead><tr><th>日期</th><th>得分</th><th>正确率</th><th></th></tr></thead><tbody>
      ${h.papers.map(p=>{const pc=p.total?Math.round(p.score/p.total*100):0;
        return `<tr><td>${p.day}</td><td class="n">${p.score}/${p.total}</td>
          <td><div class="row"><div class="prog" style="width:64px"><i class="${pc>=80?'done':''}" style="width:${pc}%"></i></div><span class="n" style="font-size:11.5px">${pc}%</span></div></td>
          <td>${p.submitted_at?'<span class="tag cyan">完成</span>':'<span class="tag">未交</span>'}</td></tr>`;
      }).join('')}</tbody></table>`:'<div class="empty">暂无记录</div>';
  }catch(e){ toast(e.message); }
}

function renderExam(p){
  if(p.submitted){
    const pct=p.total?Math.round(p.score/p.total*100):0;
    const xp=p.score*10+(p.score===p.total?20:0);
    const stars=pct>=100?3:pct>=60?2:pct>=30?1:0;
    const title=pct>=100?'全部正确':pct>=80?'大部分正确':pct>=50?'约一半正确':'多数答错';
    $('#examCard').innerHTML=`<div class="result">
      <div class="result-score">${pct}<span class="pc">%</span></div>
      <div class="result-lbl">${title} · ${p.score}/${p.total} 题</div>
      <div class="stars" style="justify-content:center;margin-top:14px">${[0,1,2].map(i=>`<i class="${i<stars?'on':''}" style="font-size:20px">★</i>`).join('')}</div>
      <div><span class="result-xp">+${xp} XP</span></div>
      <div class="rev">
        ${p.questions.map((q,i)=>{
          const ok=q.is_correct;
          const isFill=q.qtype==='fill';
          const mine=isFill?q.user_text:(q.options[q.user_choice]||'');
          const right=isFill?(q.answer_text||'').split('|')[0]:(q.options[q.answer_index]||'');
          return `<div class="rev-i">
            <div style="display:flex;gap:10px;align-items:flex-start">
              <span class="q-no ${ok?'ok':'no'}">${i+1}</span>
              <div style="flex:1">
                <div class="rev-q">${esc(q.stem)}</div>
                ${q.blank?`<div class="rev-blank">${esc(q.blank)}</div>`:''}
                ${!ok?`<div class="rev-line"><span class="mk-bad">你的答案</span><span>${esc(mine||'未作答')}</span></div>`:''}
                <div class="rev-line"><span class="mk-good">${ok?'答案':'正确'}</span><span>${esc(right)}</span></div>
                ${q.explanation?`<div class="rev-exp">${esc(q.explanation)}</div>`:''}
              </div>
            </div></div>`;
        }).join('')}
      </div></div>`;
    confetti();
    return;
  }

  $('#examCard').innerHTML=`
    <div class="paper-head">
      <div class="ph-l">
        <div class="ph-title">English Coach · 每日测验</div>
        <div class="ph-meta">${p.day} · 共 ${p.questions.length} 题 · 限时不限</div>
      </div>
      <div class="ph-r">
        <div class="hud-i"><div class="hud-k">进度</div><div class="hud-v" id="hudProg">0 / ${p.questions.length}</div></div>
      </div>
    </div>
    <div class="paper-prog"><i id="pbar" style="width:0%"></i></div>
    <div class="paper-rule">
      <span>姓名：<i class="fill-line"></i></span>
      <span>日期：<i class="fill-line">${p.day}</i></span>
      <span>得分：<i class="fill-line"></i></span>
    </div>
    <div class="paper-note">答题说明：第 Ⅰ 卷为单项选择，每题只有一个最佳选项；第 Ⅱ 卷为填空题，按语境填入最合适的词或短语。</div>
    ${sectionTitle('Ⅰ', '单项选择', p.questions.filter(q=>q.qtype!=='fill').length)}
    ${p.questions.filter(q=>q.qtype!=='fill').map((q)=>`
      <div class="pq" data-q="${q.id}">
        <div class="pq-stem"><span class="pq-no"></span><span>${esc(q.stem)}</span></div>
        ${q.context?`<div class="pq-ctx">原句：${esc(q.context)}</div>`:''}
        <div class="opts">
          ${q.options.map((o,j)=>`<label class="opt" data-j="${j}">
            <span class="mk">${String.fromCharCode(65+j)}</span><span>${esc(o)}</span></label>`).join('')}
        </div>
      </div>`).join('')}
    ${p.questions.some(q=>q.qtype==='fill')?sectionTitle('Ⅱ', '填空', p.questions.filter(q=>q.qtype==='fill').length):''}
    ${p.questions.filter(q=>q.qtype==='fill').map((q)=>`
      <div class="pq" data-q="${q.id}" data-type="fill">
        <div class="pq-stem"><span class="pq-no"></span><span>${esc(q.stem)}</span></div>
        ${q.context?`<div class="pq-ctx">原句：${esc(q.context)}</div>`:''}
        <div class="fill-sentence">${renderBlank(q.blank)}</div>
      </div>`).join('')}
    <div class="paper-foot">
      <div class="pf-note">答错的知识点会降级，之后还会出现</div>
      <button class="btn btn-lime" id="examSubmit">交卷</button>
    </div>`;

  // 题号按整卷顺序排，不按大题重置
  document.querySelectorAll('#examCard .pq-no').forEach((el,i)=>{ el.textContent=i+1; });

  document.querySelectorAll('#examCard .pq').forEach(qEl=>{
    qEl.querySelectorAll('.opt').forEach(opt=>{
      opt.onclick=()=>{
        qEl.querySelectorAll('.opt').forEach(o=>o.classList.remove('pick'));
        opt.classList.add('pick');
        updatePaperProgress(p.questions.length);
      };
    });
    const inp=qEl.querySelector('.fill-in');
    if(inp) inp.addEventListener('input',()=>updatePaperProgress(p.questions.length));
  });
  $('#examSubmit').onclick=submitExam;
}

function sectionTitle(num, name, count){
  if(!count) return '';
  return `<div class="paper-section"><span class="ps-num">第 ${num} 卷</span><span class="ps-name">${name}</span><span class="ps-cnt">${count} 题</span></div>`;
}

function renderBlank(blank){
  if(!blank) return '';
  const input=`<input class="fill-in" spellcheck="false" autocomplete="off" placeholder="填写答案">`;
  return String(blank).split('___').map(esc).join(input);
}

function updatePaperProgress(total){
  const qs=[...document.querySelectorAll('#examCard .pq')];
  const done=qs.filter(x=>{
    const pick=x.querySelector('.opt.pick');
    const inp=x.querySelector('.fill-in');
    return pick || (inp && inp.value.trim());
  }).length;
  $('#hudProg').textContent=`${done} / ${total}`;
  $('#pbar').style.width=(done/total*100)+'%';
}

async function submitExam(){
  const answers={};
  document.querySelectorAll('#examCard .pq').forEach(qEl=>{
    const sel=qEl.querySelector('.opt.pick');
    const inp=qEl.querySelector('.fill-in');
    if(sel) answers[qEl.dataset.q]=Number(sel.dataset.j);
    else if(inp && inp.value.trim()) answers[qEl.dataset.q]=inp.value.trim();
  });
  const total=curPaper.questions.length;
  const missing=total-Object.keys(answers).length;
  if(missing>0 && !confirm(`还有 ${missing} 题没答，确定交？`)) return;
  const btn=$('#examSubmit'); btn.disabled=true; btn.innerHTML='<span class="spin d"></span> 批改中';
  try{
    const r=await api('/api/exam/submit',{method:'POST',body:{paper_id:curPaper.paper_id,answers}});
    curPaper=r; renderExam(r);
    if(r.score!==r.total) $('#examCard').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){ toast(e.message); btn.disabled=false; btn.textContent='交卷'; }
}

$('#examRegen').onclick=async()=>{
  if(!confirm('换一批会清掉今天的卷子，确定？')) return;
  $('#examCard').innerHTML='<div class="pad"><div class="empty"><span class="ic">✦</span>出题中…</div></div>';
  try{
    const p=await api('/api/exam/regenerate',{method:'POST'}); curPaper=p;
    if(p.empty) loadExam(); else renderExam(p);
  }catch(e){ toast(e.message); loadExam(); }
};

/* ====== 本周重点 ====== */
async function loadPlan(){
  $('#planBody').innerHTML='<div class="empty">读取中…</div>';
  try{
    const d=await api('/api/plan/latest');
    if(!d||!d.markdown){
      $('#planBody').innerHTML='<div class="empty"><span class="ic">✦</span>还没有重点<br>选个时间范围，点「生成」</div>'; return; }
    renderPlan(d.data,d.markdown);
  }catch(e){ toast(e.message); }
}

function renderPlan(data,md){
  const focus=(data&&data.focus)||[];
  if(!focus.length){
    $('#planBody').innerHTML=`<div style="white-space:pre-wrap;font-size:13px;line-height:1.85;color:var(--text-2)">${esc(md)}</div>`;
    return;
  }
  let h=`<div class="ct" style="margin-bottom:14px">${esc(data.window||'')}</div>`;
  if(data.summary) h+=`<div class="sum">${esc(data.summary)}</div>`;
  focus.forEach((f,i)=>{
    const b=f.better||{};
    const lvs=[['平级','peer'],['对上级','manager'],['正式','formal_email']]
      .filter(([,k])=>b[k]).map(([l,k])=>`<div class="lvr"><div class="lvk">${l}</div><div class="lvv">${esc(b[k])}</div></div>`).join('');
    h+=`<div class="fc">
      <div class="fc-h"><div class="fc-n">${i+1}</div><div class="fc-t">${esc(f.topic||'')}</div></div>
      <div class="fc-d">${esc(f.diagnosis||'')}</div>
      ${f.your_sentence?`<div class="src">${esc(f.your_sentence)}</div>`:''}
      ${lvs?`<div class="lvs">${lvs}</div>`:''}
      ${f.pattern?`<div class="pat"><b>句型</b>${esc(f.pattern)}</div>`:''}
      ${f.drill?`<div class="drill"><b>练这个</b>${esc(f.drill)}</div>`:''}
    </div>`;
  });
  if((data.repeat_alert||[]).length){
    h+=`<div class="ct" style="margin:22px 0 10px">反复没改掉的</div>`;
    h+=data.repeat_alert.map(x=>`<div class="why" style="padding-left:2px">· ${esc(x)}</div>`).join('');
  }
  if(data.next_week) h+=`<div class="drill" style="margin-top:20px"><b>这周只做</b>${esc(data.next_week)}</div>`;
  $('#planBody').innerHTML=h;
}

$('#planGen').onclick=async()=>{
  const btn=$('#planGen'); btn.disabled=true; btn.innerHTML='<span class="spin d"></span> 生成中';
  $('#planBody').innerHTML='<div class="empty"><span class="ic">✦</span>正在看你最近记的东西…<br>约 10 秒</div>';
  try{
    const d=await api('/api/analyze?days='+$('#planDays').value,{method:'POST'});
    if(d.empty) $('#planBody').innerHTML=`<div class="empty">${esc(d.message)}</div>`;
    else renderPlan(d.data,d.markdown);
  }catch(e){
    toast(e.message);
    $('#planBody').innerHTML=`<div class="empty">生成失败：${esc(e.message)}</div>`;
  }finally{ btn.disabled=false; btn.textContent='生成'; }
};

/* ====== 进度 ====== */
async function loadStats(){
  try{
    const d=await api('/api/stats?weeks=8');
    const t=d.totals;
    $('#statMetrics').innerHTML=[
      ['Level '+t.level,t.xp,'XP','到下一级还需 '+(t.level_next-t.level_xp)],
      ['累计记录',t.reports,'条','8 周内'],
      ['做题',t.questions,'题',t.accuracy==null?'':('正确率 '+t.accuracy+'%')],
      ['连续',t.streak,'天','有记录或练习']
    ].map(([k,v,u,dd])=>`<div class="mt"><div class="mt-k">${k}</div>
      <div class="mt-v">${v}<small>${u}</small></div><div class="mt-d">${dd}</div></div>`).join('');

    const max=Math.max(1,...d.weeks.map(w=>w.reports));
    $('#statChart').innerHTML=d.weeks.map(w=>`
      <div class="bw">
        <div class="bv">${w.reports||''}</div>
        <div class="bar" style="height:${Math.max(4,w.reports/max*124)}px" title="${w.week_start} ~ ${w.week_end}：${w.reports} 条"></div>
        <div class="bl">${fmtD(w.week_start)}</div>
      </div>`).join('');

    $('#statTable').innerHTML=`<table class="t">
      <thead><tr><th>周</th><th>记录</th><th>活跃天</th><th>练习</th><th>题数</th><th>正确率</th></tr></thead><tbody>
      ${d.weeks.slice().reverse().map(w=>`<tr>
        <td>${fmtD(w.week_start)} – ${fmtD(w.week_end)}</td>
        <td class="n">${w.reports}</td><td class="n">${w.days_active}</td>
        <td class="n">${w.exams}</td><td class="n">${w.questions}</td>
        <td>${w.accuracy==null?'<span style="color:var(--text-3)">—</span>':`<div class="row"><div class="prog" style="width:58px"><i class="${w.accuracy>=80?'done':''}" style="width:${w.accuracy}%"></i></div><span class="n" style="font-size:11.5px">${w.accuracy}%</span></div>`}</td>
      </tr>`).join('')}</tbody></table>`;

    const cards=d.cards||[];
    $('#statCards').innerHTML=cards.length?`<table class="t">
      <thead><tr><th>知识点</th><th>出现</th><th>熟练度</th><th>答题</th></tr></thead><tbody>
      ${cards.map(c=>`<tr>
        <td><div style="font-weight:560">${esc(c.topic)}</div>
          ${c.canonical?`<div style="font-size:11.5px;color:var(--text-3);margin-top:2px">${esc(c.canonical)}</div>`:''}</td>
        <td class="n">${c.occurrences}</td>
        <td><div class="row"><div class="prog" style="width:66px"><i class="${c.level>=5?'done':''}" style="width:${c.level/5*100}%"></i></div>
          <span class="n" style="font-size:11px;color:var(--text-3)">${c.level}/5</span></div></td>
        <td class="n">${c.attempts?c.correct_count+'/'+c.attempts:'<span style="color:var(--text-3)">—</span>'}</td>
      </tr>`).join('')}</tbody></table>`:'<div class="empty">还没有知识点</div>';
  }catch(e){ toast(e.message); }
}

/* ====== 成员 ====== */
async function loadUsers(){
  try{
    const d=await api('/api/users');
    $('#userTable').innerHTML=`<table class="t">
      <thead><tr><th>成员</th><th>角色</th><th>XP</th><th>API Key</th><th>加入</th><th></th></tr></thead><tbody>
      ${d.users.map(u=>`<tr>
        <td><div class="row" style="gap:9px">
          <div class="av" style="width:26px;height:26px;font-size:11px;border-radius:8px">${esc((u.display_name||u.username)[0].toUpperCase())}</div>
          <div><div style="font-weight:560">${esc(u.display_name||u.username)}</div>
            <div style="font-size:11px;color:var(--text-3)">@${esc(u.username)}</div></div></div></td>
        <td>${u.role==='admin'?'<span class="tag violet">管理员</span>':'<span class="tag">成员</span>'}</td>
        <td class="n">${u.xp||0}</td>
        <td><span class="apikey" data-k="${esc(u.api_key)}">${esc(u.api_key.slice(0,12))}…</span></td>
        <td style="color:var(--text-3);font-size:11.5px">${esc((u.created_at||'').slice(0,10))}</td>
        <td>${u.id===ME.id?'<span style="font-size:11.5px;color:var(--text-3)">当前</span>':`<button class="btn btn-sm btn-ghost" data-del="${u.id}">删除</button>`}</td>
      </tr>`).join('')}</tbody></table>`;
    document.querySelectorAll('.apikey').forEach(el=>{
      el.onclick=()=>{ navigator.clipboard.writeText(el.dataset.k); toast('API Key 已复制'); };
    });
    document.querySelectorAll('[data-del]').forEach(b=>{
      b.onclick=async()=>{
        if(!confirm('删除成员会连同其所有数据一起删掉，确定？')) return;
        try{ await api('/api/users/delete',{method:'POST',body:{id:Number(b.dataset.del)}}); toast('已删除'); loadUsers(); }
        catch(e){ toast(e.message); }
      };
    });
  }catch(e){ toast(e.message); }
}

$('#userAdd').onclick=()=>$('#mUser').classList.add('on');
$('#nuCancel').onclick=()=>$('#mUser').classList.remove('on');
$('#nuSubmit').onclick=async()=>{
  const btn=$('#nuSubmit'); btn.disabled=true;
  try{
    await api('/api/users',{method:'POST',body:{
      username:$('#nuName').value.trim(), display_name:$('#nuDisp').value.trim(), password:$('#nuPw').value}});
    $('#mUser').classList.remove('on'); toast('成员已创建'); loadUsers();
    $('#nuName').value=''; $('#nuDisp').value=''; $('#nuPw').value='';
  }catch(e){ toast(e.message); }
  finally{ btn.disabled=false; }
};

/* ====== 启动 ====== */
function boot(){
  if(!ME) return;
  $('#login').style.display='none';
  $('#app').style.display='block';
  $('#whoName').textContent=ME.display_name||ME.username;
  $('#whoRole').textContent=ME.role==='admin'?'管理员':'成员';
  $('#av').textContent=(ME.display_name||ME.username)[0].toUpperCase();
  $('#navUsers').style.display=ME.role==='admin'?'':'none';
  $('#repDate').value=selDay;
  showView('dash');
}

(async function init(){
  if(!TOKEN) return;
  try{
    const d=await api('/api/me');
    ME=d.user; boot();
  }catch(e){ /* api() 已处理 */ }
})();
