"use strict";
const $ = id => document.getElementById(id);
const terminal = new Set(["completed", "cancelled", "failed", "interrupted"]);
const labels = {uploading:"上傳中",validating:"檢查媒體",loading_model:"載入模型",decoding:"解碼音軌",transcribing:"轉錄中",cancelling:"取消中",completed:"已完成",cancelled:"已取消（部分結果）",failed:"失敗（部分結果）",interrupted:"已中斷（部分結果）"};
let csrf = "", selected = null, active = null, stream = null, xhr = null;
let pendingRows = new Map();
let rows = [], lastId = 0, generation = 0, modelsReady = false, historyOffset = 0;
let activePollPending = false;
let submitting = false;
let controlPending = false;
const executionLabels={pause_requested:"等待目前推論完成後暫停",paused:"已暫停轉錄",resume_requested:"繼續轉錄中…"};
let editor = null;
const workspaceStorageKey = "local-transcript.workspace-cleared";
let workspaceCleared = false;
try { workspaceCleared = sessionStorage.getItem(workspaceStorageKey) === "1"; } catch { /* 僅保留本次頁面的狀態。 */ }
const emptyMark = $("transcript").querySelector?.(".empty-mark")?.cloneNode(true);
function message(text="") { $("message").textContent = text; }
function time(seconds) { if(!Number.isFinite(seconds)||seconds<0)return "未知"; const n=Math.round(seconds);return [Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(x=>String(x).padStart(2,"0")).join(":"); }
async function api(path, options={}) {
  const response = await fetch(path,{...options,headers:{"X-CSRF-Token":csrf,...options.headers}});
  if(!response.ok){let error;try{error=await response.json();}catch{error={message:"本機服務回應異常。"};}throw new Error(error.message);}
  return response.status===204?null:response.json();
}
function canClearWorkspace(){ return !!selected&&terminal.has(selected.status)&&!active&&!submitting&&!xhr&&!editor?.dirty.size&&!editor?.saving&&!editor?.inserting; }
function updateButtons(){
  $("start").disabled=!!active||submitting||!modelsReady;
  $("cancel").disabled=!selected||selected.job_id!==active||selected.status==="cancelling";
  const state=selected?.execution_state||"running";
  const pending=state==="pause_requested"||state==="resume_requested";
  $("pauseResume").disabled=controlPending||!selected||selected.job_id!==active||selected.status!=="transcribing"||pending;
  $("pauseResume").textContent=state==="paused"?"繼續轉錄":state==="pause_requested"?"等待暫停…":state==="resume_requested"?"等待繼續…":"暫停轉錄";
  $("pauseHint").textContent=!selected?"":selected.status==="transcribing"?state==="paused"?"模型與音訊仍占用記憶體，可繼續校稿。關閉服務後無法接續。":pending?(selected.control_wait_sec>=15?"仍在等待工作程序確認；可取消轉錄，已保存文字不受影響。":"正在等待工作程序確認，已產生的片段會先保存。"):"暫停會等待目前推論完成；不會立即中止原生運算。":"模型載入與解碼期間無法暫停；已結束的任務無法接續。";
  $("clearWorkspace").disabled=!canClearWorkspace();
  const hint=active||submitting||xhr?"有活動任務，請待完成或取消後再清除。":!selected?"請先開啟一份已結束的紀錄。":!terminal.has(selected.status)?"此任務尚未結束，暫時無法清除。":"只清空顯示，歷史結果保留。";
  $("clearWorkspace").title=hint;$("clearWorkspaceHint").textContent=hint;
}
function rememberWorkspaceCleared(value){
  workspaceCleared=value;
  try { if(value)sessionStorage.setItem(workspaceStorageKey,"1");else sessionStorage.removeItem(workspaceStorageKey); } catch { /* 儲存受阻仍可清除目前畫面。 */ }
}
function resetWorkspace({cleared=false,deleted=false}={}){
  ++generation;
  if(stream){stream.close();stream=null;}
  selected=null;rows=[];lastId=0;pendingRows.clear();
  editor=null;showSaveStatus();
  $("jobTitle").textContent="逐字稿";
  $("progress").value=0;
  $("status").textContent=cleared?"工作區已清除，結果仍保留於本機歷史。":deleted?"結果已刪除。":"選擇檔案，開始第一份會議逐字稿。";
  $("timing").textContent="模型載入與解碼期間不顯示估計百分比。";
  message();
  const box=document.createElement("div");box.className="empty";
  if(emptyMark)box.append(emptyMark.cloneNode(true));
  const title=document.createElement("h3");title.textContent=cleared?"工作區已清除":deleted?"結果已刪除":"從一段錄音開始";
  const detail=document.createElement("p");detail.textContent=cleared?"結果仍保留於本機歷史，選取紀錄即可重新開啟。":deleted?"請從本機歷史選擇其他紀錄。":"完成一段，就會在這裡留下帶時間戳的文字。";
  box.append(title,detail);$("transcript").replaceChildren(box);
  $("copy").disabled=true;
  document.querySelectorAll("[data-format]").forEach(button=>button.disabled=true);
  updateButtons();
}
function clearWorkspace(){
  if(!canClearWorkspace())return false;
  rememberWorkspaceCleared(true);
  resetWorkspace({cleared:true});
  $("transcript").focus?.();
  return true;
}
async function restoreWorkspace(ticket=generation){
  if(ticket!==generation)return;
  if(workspaceCleared){resetWorkspace({cleared:true});return;}
  try{
    const latest=await api("/api/jobs/latest");
    if(ticket===generation&&!workspaceCleared&&latest)await selectJob(latest.job_id);
  }catch(error){if(ticket===generation&&!workspaceCleared)throw error;}
}
function showMeta(meta){
  if(selected?.job_id===meta.job_id){
    if(terminal.has(selected.status)&&!terminal.has(meta.status))return;
    if(selected.status==="cancelling"&&meta.status!=="cancelling"&&!terminal.has(meta.status))return;
    if(!terminal.has(meta.status)){
      if((meta.control_seq??0)<(selected.control_seq??0))return;
      if((meta.control_seq??0)===(selected.control_seq??0)&&
        ((meta.execution_state==="pause_requested"&&selected.execution_state==="paused")||
         (meta.execution_state==="resume_requested"&&selected.execution_state==="running")))return;
    }
  }
  selected=meta;
  if(!terminal.has(meta.status))active=meta.job_id;else if(active===meta.job_id)active=null;
  $("jobTitle").textContent=meta.request.title||meta.request.source_name;
  $("status").textContent=(meta.status==="transcribing"&&executionLabels[meta.execution_state])||labels[meta.status]||meta.status;
  const ratio=meta.duration_sec?Math.min(99,100*meta.last_position_sec/meta.duration_sec):null;
  if(meta.status==="completed")$("progress").value=100;
  else if(meta.status==="transcribing"&&ratio!==null)$("progress").value=ratio;
  else if(terminal.has(meta.status))$("progress").value=ratio||0;
  else $("progress").removeAttribute("value");
  const activeElapsed=meta.active_elapsed_sec??meta.elapsed_sec;
  const running=!meta.execution_state||meta.execution_state==="running";
  const eta=meta.status==="transcribing"&&running&&meta.last_position_sec>=60&&activeElapsed>=10&&meta.duration_sec?time(Math.max(0,activeElapsed*(meta.duration_sec/meta.last_position_sec-1))):running?"估算中":"暫停估算";
  $("timing").textContent=`已保存位置 ${time(meta.last_position_sec)} / ${time(meta.duration_sec)} · 總耗時 ${time(meta.elapsed_sec)} · 活動 ${time(activeElapsed)} · 暫停 ${time(meta.paused_sec??0)} · 剩餘 ${terminal.has(meta.status)?"—":eta}（估計）`;
  if(meta.error)message(meta.error);else if(meta.warnings.length)message(meta.warnings.join("\n"));
  document.querySelectorAll("[data-format]").forEach(b=>b.disabled=false);
  $("copy").disabled=!editedRows().length;updateButtons();
}
function append(row){
  if(row.segment_id<=lastId)return;
  pendingRows.set(row.segment_id,row);
  while(pendingRows.has(lastId+1)){
    const next=pendingRows.get(lastId+1);pendingRows.delete(lastId+1);appendNext(next);
  }
}
function appendNext(row){
  if(!rows.length&&!editor?.ready)$("transcript").replaceChildren();
  rows.push(row);lastId=row.segment_id;
  const node=document.createElement("div");node.className="segment";
  const stamp=document.createElement("time");stamp.textContent=time(row.start_sec);
  const text=editor?.ready?editControls(row):document.createElement("span");
  if(!editor?.ready)text.textContent=row.text;
  node.append(stamp,text);$("transcript").append(node);$("copy").disabled=false;
  if(editor?.ready){editor.sourceNodes.set(row.segment_id,node);mountInserted(row.segment_id);}
}
function showSaveStatus(){
  $("saveStatus").textContent=!editor?"":editor.error?editor.error:editor.saving?"儲存中…":editor.dirty.size?"等待儲存…":editor.ready?"修訂已儲存於本機":"載入修訂中…";
  $("retrySave").hidden=!editor?.error;
  $("addFirst").disabled=!editor?.ready||!!editor?.inserting;
}
function queueRevision(row){
  editor.map.set(row.key,row);editor.dirty.set(row.key,row);editor.error="";
  clearTimeout(editor.timer);editor.timer=setTimeout(()=>flushEdits(),600);
  showSaveStatus();updateButtons();
}
async function flushEdits(){
  const current=editor;
  if(!current)return true;
  clearTimeout(current.timer);
  if(current.saving){await current.saving;if(current.error)return false;return flushEdits();}
  current.saving=(async()=>{
    try{
      while(current.dirty.size){
        const [key,row]=current.dirty.entries().next().value;
        const state=await api(`/api/jobs/${current.job}/revisions`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({version:current.version,row})});
        current.version=state.version;
        if(current.dirty.get(key)===row)current.dirty.delete(key);
      }
      current.error="";
    }catch(error){current.error=`尚未儲存：${error.message}`;}
  })();
  showSaveStatus();updateButtons();
  await current.saving;current.saving=null;
  showSaveStatus();updateButtons();
  return !current.dirty.size&&!current.error;
}
function editControls(original,inserted=null){
  const key=inserted?.key||`s:${original.segment_id}`;
  const box=document.createElement("div");box.className="segment-editor";
  const input=document.createElement("textarea");input.rows=2;input.maxLength=12000;
  input.setAttribute?.("aria-label",inserted?"新增語句":"修訂語句");
  let value=editor.map.get(key)||{key,anchor:0,text:original.text,deleted:false,start_sec:null,end_sec:null};
  input.value=value.text;input.disabled=value.deleted;
  const tools=document.createElement("div");tools.className="edit-tools";
  const remove=document.createElement("button");remove.type="button";remove.className="secondary";
  remove.textContent=value.deleted?"復原刪除":"刪除語句";
  function change(patch){value={...value,...patch};queueRevision(value);}
  input.oninput=()=>change({text:input.value});
  remove.onclick=()=>{change({deleted:!value.deleted});input.disabled=value.deleted;remove.textContent=value.deleted?"復原刪除":"刪除語句";};
  tools.append(remove);
  if(!inserted){
    const restore=document.createElement("button");restore.type="button";restore.className="secondary";restore.textContent="還原原文";
    restore.onclick=()=>{input.value=original.text;input.disabled=false;remove.textContent="刪除語句";change({text:original.text,deleted:false});};
    tools.append(restore);
    for(const [label,anchor] of [["前方新增",original.segment_id-1],["後方新增",original.segment_id]]){
      const add=document.createElement("button");add.type="button";add.className="secondary";add.textContent=label;
      add.onclick=()=>insertSentence(anchor);tools.append(add);
    }
  }else{
    const fields={};
    for(const [name,label] of [["start_sec","開始秒數"],["end_sec","結束秒數"]]){
      const field=document.createElement("input");field.type="number";field.min="0";field.step="0.001";field.placeholder=label;field.value=value[name]??"";field.setAttribute?.("aria-label",label);fields[name]=field;tools.append(field);
      field.oninput=()=>change({start_sec:fields.start_sec.value===""?null:Number(fields.start_sec.value),end_sec:fields.end_sec.value===""?null:Number(fields.end_sec.value)});
    }
  }
  box.append(input,tools);return box;
}
function mountInserted(anchor){
  if(!editor?.ready)return;
  for(const row of editor.map.values()){
    if(!row.key.startsWith("u:")||row.anchor!==anchor||editor.mounted.has(row.key))continue;
    editor.mounted.add(row.key);
    const node=document.createElement("div");node.className="segment inserted";node.dataset.key=row.key;
    const stamp=document.createElement("time");stamp.textContent="人工新增";
    node.append(stamp,editControls(null,row));
    const next=editor.sourceNodes.get(anchor+1);
    if(next)$("transcript").insertBefore(node,next);else $("transcript").append(node);
  }
}
async function insertSentence(anchor){
  const current=editor;
  if(!current?.ready||current.inserting)return;
  current.inserting=true;showSaveStatus();
  try{
    if(!await flushEdits())return;
    current.saving=api(`/api/jobs/${current.job}/revisions`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({version:current.version,row:{key:"new",anchor,text:"",deleted:false,start_sec:null,end_sec:null}})});
    showSaveStatus();
    const state=await current.saving;current.version=state.version;
    for(const row of state.rows)if(!current.dirty.has(row.key))current.map.set(row.key,row);
    mountInserted(anchor);
  }catch(error){current.error=`新增失敗：${error.message}`;}
  finally{current.saving=null;current.inserting=false;$("copy").disabled=!editedRows().length;showSaveStatus();updateButtons();}
}
function editedRows(){
  if(!editor?.ready)return rows;
  const result=[];
  const extras=anchor=>{for(const row of editor.map.values())if(row.key.startsWith("u:")&&row.anchor===anchor&&!row.deleted)result.push({...row,start_sec:row.start_sec??NaN});};
  extras(0);
  for(const row of rows){const changed=editor.map.get(`s:${row.segment_id}`);if(!changed?.deleted)result.push({...row,text:changed?changed.text:row.text});extras(row.segment_id);}
  return result;
}
async function selectJob(id){
  const before=generation;
  if(editor?.inserting)return false;
  if(editor&&!await flushEdits())return false;
  if(before!==generation)return false;
  rememberWorkspaceCleared(false);
  const ticket=++generation;
  if(stream)stream.close();
  rows=[];lastId=0;pendingRows.clear();selected=null;editor=null;showSaveStatus();updateButtons();$("copy").disabled=true;document.querySelectorAll("[data-format]").forEach(b=>b.disabled=true);message();$("transcript").replaceChildren();
  try{
    const meta=await api(`/api/jobs/${id}`);if(ticket!==generation)return;showMeta(meta);
    editor={job:id,ready:false,version:0,map:new Map(),dirty:new Map(),mounted:new Set(),sourceNodes:new Map(),saving:null,error:""};showSaveStatus();
    const revision=await api(`/api/jobs/${id}/revisions`);if(ticket!==generation)return;
    editor.version=revision.version||0;editor.map=new Map((revision.rows||[]).map(row=>[row.key,row]));editor.ready=true;
    mountInserted(0);showSaveStatus();
    // 先接收事件，再補查快照；同一份片段依 ID 去重。
    if(!terminal.has(meta.status))connect(id,ticket);
    while(ticket===generation){
      const page=await api(`/api/jobs/${id}/segments?after_id=${lastId}`);
      if(ticket!==generation)return;
      page.forEach(append);if(page.length<200)break;
    }
  }catch(error){if(ticket===generation)message(error.message);}
}
function connect(id,ticket){
  stream=new EventSource(`/api/jobs/${id}/events?after_id=${lastId}`);
  stream.addEventListener("status",e=>{if(ticket===generation)showMeta(JSON.parse(e.data));});
  stream.addEventListener("segment",e=>{if(ticket!==generation)return;const row=JSON.parse(e.data);append(row);});
  const end=async e=>{if(ticket!==generation||!e.data)return;stream.close();try{const meta=await api(`/api/jobs/${id}`);if(ticket===generation)showMeta(meta);await history();}catch(error){if(ticket===generation)message(error.message);}};
  ["done","cancelled","error"].forEach(kind=>stream.addEventListener(kind,end));
  stream.onerror=e=>{if(!e.data&&ticket===generation)message("連線中斷，正在重連；伺服器工作不受影響。");};
}
async function history(more=false){
  const offset=more?historyOffset:0;
  const result=await api(`/api/jobs?offset=${offset}&limit=50`);
  active=result.active_id;updateButtons();
  if(!more)$("history").replaceChildren();
  for(const meta of result.items){
    const row=document.createElement("div");row.className="historyrow";
    const name=document.createElement("button");name.className="secondary name";
    name.textContent=meta.request.title||meta.request.source_name;
    const detail=document.createElement("small");detail.textContent=`${new Date(meta.created_at).toLocaleString()} · ${(meta.status==="transcribing"&&executionLabels[meta.execution_state])||labels[meta.status]} · ${time(meta.last_position_sec)}`;
    name.append(detail);name.onclick=()=>selectJob(meta.job_id);row.append(name);
    if(terminal.has(meta.status)){const del=document.createElement("button");del.className="secondary delete";del.textContent="刪除";del.onclick=async()=>{if(!confirm("刪除這份本機結果？原始錄影不受影響。"))return;try{if(selected?.job_id===meta.job_id&&(editor?.inserting||!await flushEdits()))return;await api(`/api/jobs/${meta.job_id}`,{method:"DELETE"});if(selected?.job_id===meta.job_id)resetWorkspace({deleted:true});await history();}catch(e){message(e.message);}};row.append(del);}
    $("history").append(row);
  }
  historyOffset=offset+result.items.length;$("more").hidden=historyOffset>=result.total;
}
// 檢視歷史結果會關閉其餘 SSE；活動槽位仍需獨立更新，不改動目前預覽。
async function refreshActive(){
  if(!active||activePollPending)return;
  const observed=active;activePollPending=true;
  try{
    const result=await api("/api/jobs?limit=1");
    if(active!==observed)return;
    active=result.active_id;updateButtons();
    if(active!==observed)await history();
  }catch{ /* 暫時離線時保留目前狀態，下次輪詢重試。 */ }
  finally{activePollPending=false;}
}
$("form").onsubmit=async event=>{
  event.preventDefault();message();if(active||submitting)return;
  if(editor?.inserting)return;
  const file=$("file").files[0];if(!file)return;
  submitting=true;updateButtons();
  try{
    if(editor&&!await flushEdits())return;
    const meta=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_name:file.name,expected_bytes:file.size,title:$("title").value,meeting_date:$("date").value,participants:$("participants").value,terms:$("terms").value,model:$("model").value,language:$("language").value,condition_on_previous_text:$("previousContext").checked,cpu_threads:Number($("threads").value),traditional:$("traditional").checked})});
    active=meta.job_id;await selectJob(meta.job_id);await history();
    await new Promise((resolve,reject)=>{xhr=new XMLHttpRequest();xhr.open("PUT",`/api/jobs/${meta.job_id}/media`);xhr.setRequestHeader("Content-Type","application/octet-stream");xhr.setRequestHeader("X-CSRF-Token",csrf);xhr.upload.onprogress=e=>{if(selected?.job_id===meta.job_id&&e.lengthComputable){$("progress").value=e.loaded/e.total*100;$("timing").textContent=`上傳 ${Math.round(e.loaded/e.total*100)}%（與轉錄進度分開）`;}};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{let text="上傳失敗";try{text=JSON.parse(xhr.responseText).message;}catch{}reject(new Error(text));}};xhr.onerror=()=>reject(new Error("上傳連線中斷。"));xhr.onabort=()=>reject(new Error("上傳已中止。"));xhr.send(file);});
  }catch(error){message(error.message);}finally{submitting=false;xhr=null;try{await history();}catch{}updateButtons();}
};
$("clearWorkspace").onclick=clearWorkspace;
$("pauseResume").onclick=async()=>{
  if(!selected||selected.job_id!==active||selected.status!=="transcribing"||controlPending)return;
  const id=selected.job_id,ticket=generation,state=selected.execution_state||"running";
  if(state!=="running"&&state!=="paused")return;
  controlPending=true;updateButtons();
  try{
    const meta=await api(`/api/jobs/${id}/${state==="paused"?"resume":"pause"}`,{method:"POST"});
    if(ticket===generation&&selected?.job_id===id)showMeta(meta);
  }catch(error){if(ticket===generation)message(error.message);}
  finally{controlPending=false;updateButtons();}
};
$("cancel").onclick=async()=>{
  if(!selected)return;
  const id=selected.job_id,ticket=generation;
  try{
    const promise=api(`/api/jobs/${id}/cancel`,{method:"POST"});
    if(xhr)xhr.abort();
    const meta=await promise;
    if(ticket===generation&&selected?.job_id===id)showMeta(meta);
  }catch(error){if(ticket===generation)message(error.message);}
};
$("copy").onclick=async()=>{
  if(!selected||!editedRows().length)return;
  const ticket=generation;
  if(editor?.inserting||editor&&!editor.ready)return;
  if(editor&&!await flushEdits())return;
  if(ticket!==generation||!selected)return;
  const text=`狀態：${labels[selected.status]}${selected.status!=="completed"?"（不完整）":""}\n`+editedRows().map(r=>`[${Number.isFinite(r.start_sec)?time(r.start_sec):"時間未設定"}] ${r.text}`).join("\n");
  try{
    await navigator.clipboard.writeText(text);
    if(ticket===generation)message("逐字稿已複製。");
  }catch{
    if(ticket!==generation)return;
    message("無法自動複製，請選取預覽文字後按 Ctrl+C。");
    const range=document.createRange();range.selectNodeContents($("transcript"));
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
  }
};

document.querySelectorAll("[data-format]").forEach(button=>button.onclick=async()=>{
  if(!selected)return;
  const ticket=generation;
  if(editor?.inserting||(editor&&!editor.ready&&button.dataset.variant!=="raw"))return;
  if(editor&&!await flushEdits())return;
  if(ticket!==generation||!selected)return;
  try{
    const response=await fetch(`/api/jobs/${selected.job_id}/export?format=${button.dataset.format}&variant=${button.dataset.variant||"display"}`);
    if(!response.ok)throw new Error((await response.json()).message);
    const url=URL.createObjectURL(await response.blob());
    const link=document.createElement("a");link.href=url;
    const encoded=response.headers.get("Content-Disposition")?.split("filename*=UTF-8''")[1];
    link.download=encoded?decodeURIComponent(encoded):`逐字稿.${button.dataset.format}`;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }catch(error){if(ticket===generation)message(error.message);}
});
$("retrySave").onclick=()=>flushEdits();
$("addFirst").onclick=()=>insertSentence(0);
if(typeof window!=="undefined")window.addEventListener("beforeunload",event=>{if(editor?.dirty.size||editor?.saving||editor?.inserting){event.preventDefault();event.returnValue="";}});
$("refresh").onclick=()=>history().catch(e=>message(e.message));$("more").onclick=()=>history(true).catch(e=>message(e.message));
$("file").onchange=()=>{const file=$("file").files[0];if(file)$("fileInfo").textContent=`${file.name} · ${(file.size/1024**2).toFixed(1)} MiB`;};
const zone=$("dropzone");zone.ondragover=e=>{e.preventDefault();zone.classList.add("over");};zone.ondragleave=()=>zone.classList.remove("over");zone.ondrop=e=>{e.preventDefault();zone.classList.remove("over");if(e.dataTransfer.files.length!==1){message("請一次選擇一個檔案。");return;}$("file").files=e.dataTransfer.files;$("file").onchange();};
async function init(){
  const ticket=generation;
  const config=await api("/api/session");csrf=config.csrf;
  const models=await api("/api/models");$("model").replaceChildren();
  for(const model of models){const option=document.createElement("option");option.value=model.name;option.textContent=`${model.name} · ${model.ready?"已準備":"未準備"}`;option.disabled=!model.ready;$("model").append(option);}
  modelsReady=models.some(m=>m.ready);if(models.find(m=>m.name==="small").ready)$("model").value="small";
  $("modelHelp").textContent=modelsReady?"small 為預設；medium／large-v3-turbo 請先以短片段比較品質、耗時與記憶體。":"尚無可用模型。請先依 README 執行 prepare_models.py，再重新整理頁面。";
  await history();await restoreWorkspace(ticket);updateButtons();
  setInterval(refreshActive,2000);
}
init().catch(e=>message(e.message));
