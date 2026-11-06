"use strict";
const $ = id => document.getElementById(id);
const terminal = new Set(["completed", "cancelled", "failed", "interrupted"]);
const labels = {uploading:"上傳中",validating:"檢查媒體",loading_model:"載入模型",decoding:"解碼音軌",transcribing:"轉錄中",cancelling:"取消中",completed:"已完成",cancelled:"已取消（部分結果）",failed:"失敗（部分結果）",interrupted:"已中斷（部分結果）"};
let csrf = "", selected = null, active = null, stream = null, xhr = null;
let pendingRows = new Map();
let rows = [], lastId = 0, generation = 0, modelsReady = false, historyOffset = 0;
let activePollPending = false;
function message(text="") { $("message").textContent = text; }
function time(seconds) { if(!Number.isFinite(seconds)||seconds<0)return "未知"; const n=Math.round(seconds);return [Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(x=>String(x).padStart(2,"0")).join(":"); }
async function api(path, options={}) {
  const response = await fetch(path,{...options,headers:{"X-CSRF-Token":csrf,...options.headers}});
  if(!response.ok){let error;try{error=await response.json();}catch{error={message:"本機服務回應異常。"};}throw new Error(error.message);}
  return response.status===204?null:response.json();
}
function updateButtons(){ $("start").disabled=!!active||!modelsReady; $("cancel").disabled=!selected||selected.job_id!==active||selected.status==="cancelling"; }
function showMeta(meta){
  selected=meta;
  if(!terminal.has(meta.status))active=meta.job_id;else if(active===meta.job_id)active=null;
  $("jobTitle").textContent=meta.request.title||meta.request.source_name;
  $("status").textContent=labels[meta.status]||meta.status;
  const ratio=meta.duration_sec?Math.min(99,100*meta.last_position_sec/meta.duration_sec):null;
  if(meta.status==="completed")$("progress").value=100;
  else if(meta.status==="transcribing"&&ratio!==null)$("progress").value=ratio;
  else if(terminal.has(meta.status))$("progress").value=ratio||0;
  else $("progress").removeAttribute("value");
  const eta=meta.status==="transcribing"&&meta.last_position_sec>=60&&meta.elapsed_sec>=10&&meta.duration_sec?time(Math.max(0,meta.elapsed_sec*(meta.duration_sec/meta.last_position_sec-1))):"估算中";
  $("timing").textContent=`已保存位置 ${time(meta.last_position_sec)} / ${time(meta.duration_sec)} · 耗時 ${time(meta.elapsed_sec)} · 剩餘 ${terminal.has(meta.status)?"—":eta}（估計）`;
  if(meta.error)message(meta.error);else if(meta.warnings.length)message(meta.warnings.join("\n"));
  document.querySelectorAll("[data-format]").forEach(b=>b.disabled=false);
  $("copy").disabled=!rows.length;updateButtons();
}
function append(row){
  if(row.segment_id<=lastId)return;
  pendingRows.set(row.segment_id,row);
  while(pendingRows.has(lastId+1)){
    const next=pendingRows.get(lastId+1);pendingRows.delete(lastId+1);appendNext(next);
  }
}
function appendNext(row){
  if(!rows.length)$("transcript").replaceChildren();
  rows.push(row);lastId=row.segment_id;
  const node=document.createElement("div");node.className="segment";
  const stamp=document.createElement("time");stamp.textContent=time(row.start_sec);
  const text=document.createElement("span");text.textContent=row.text;
  node.append(stamp,text);$("transcript").append(node);$("copy").disabled=false;
}
async function selectJob(id){
  const ticket=++generation;
  if(stream)stream.close();
  rows=[];lastId=0;pendingRows.clear();selected=null;updateButtons();document.querySelectorAll("[data-format]").forEach(b=>b.disabled=true);message();$("transcript").replaceChildren();
  try{
    const meta=await api(`/api/jobs/${id}`);if(ticket!==generation)return;showMeta(meta);
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
  const end=async e=>{if(ticket!==generation||!e.data)return;stream.close();await selectJob(id);await history();};
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
    const detail=document.createElement("small");detail.textContent=`${new Date(meta.created_at).toLocaleString()} · ${labels[meta.status]} · ${time(meta.last_position_sec)}`;
    name.append(detail);name.onclick=()=>selectJob(meta.job_id);row.append(name);
    if(terminal.has(meta.status)){const del=document.createElement("button");del.className="secondary delete";del.textContent="刪除";del.onclick=async()=>{if(!confirm("刪除這份本機結果？原始錄影不受影響。"))return;try{await api(`/api/jobs/${meta.job_id}`,{method:"DELETE"});if(selected?.job_id===meta.job_id){++generation;if(stream)stream.close();selected=null;rows=[];lastId=0;$("transcript").replaceChildren();$("status").textContent="結果已刪除。";document.querySelectorAll("[data-format]").forEach(b=>b.disabled=true);$("copy").disabled=true;}await history();}catch(e){message(e.message);}};row.append(del);}
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
  event.preventDefault();message();if(active)return;
  const file=$("file").files[0];if(!file)return;
  $("start").disabled=true;
  try{
    const meta=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_name:file.name,expected_bytes:file.size,title:$("title").value,meeting_date:$("date").value,participants:$("participants").value,terms:$("terms").value,model:$("model").value,language:$("language").value,condition_on_previous_text:$("previousContext").checked,cpu_threads:Number($("threads").value),traditional:$("traditional").checked})});
    active=meta.job_id;await selectJob(meta.job_id);await history();
    await new Promise((resolve,reject)=>{xhr=new XMLHttpRequest();xhr.open("PUT",`/api/jobs/${meta.job_id}/media`);xhr.setRequestHeader("Content-Type","application/octet-stream");xhr.setRequestHeader("X-CSRF-Token",csrf);xhr.upload.onprogress=e=>{if(selected?.job_id===meta.job_id&&e.lengthComputable){$("progress").value=e.loaded/e.total*100;$("timing").textContent=`上傳 ${Math.round(e.loaded/e.total*100)}%（與轉錄進度分開）`;}};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{let text="上傳失敗";try{text=JSON.parse(xhr.responseText).message;}catch{}reject(new Error(text));}};xhr.onerror=()=>reject(new Error("上傳連線中斷。"));xhr.onabort=()=>reject(new Error("上傳已中止。"));xhr.send(file);});
  }catch(error){message(error.message);}finally{xhr=null;try{await history();}catch{}updateButtons();}
};
$("cancel").onclick=async()=>{if(!selected)return;const id=selected.job_id;try{const promise=api(`/api/jobs/${id}/cancel`,{method:"POST"});if(xhr)xhr.abort();showMeta(await promise);}catch(e){message(e.message);}};
$("copy").onclick=async()=>{const text=`狀態：${labels[selected.status]}${selected.status!=="completed"?"（不完整）":""}\n`+rows.map(r=>`[${time(r.start_sec)}] ${r.text}`).join("\n");try{await navigator.clipboard.writeText(text);message("逐字稿已複製。");}catch{message("無法自動複製，請選取預覽文字後按 Ctrl+C。");const range=document.createRange();range.selectNodeContents($("transcript"));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);}};
document.querySelectorAll("[data-format]").forEach(button=>button.onclick=async()=>{
  if(!selected)return;
  try{
    const response=await fetch(`/api/jobs/${selected.job_id}/export?format=${button.dataset.format}&variant=${button.dataset.variant||"display"}`);
    if(!response.ok)throw new Error((await response.json()).message);
    const url=URL.createObjectURL(await response.blob());
    const link=document.createElement("a");link.href=url;
    const encoded=response.headers.get("Content-Disposition")?.split("filename*=UTF-8''")[1];
    link.download=encoded?decodeURIComponent(encoded):`逐字稿.${button.dataset.format}`;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }catch(error){message(error.message);}
});
$("refresh").onclick=()=>history().catch(e=>message(e.message));$("more").onclick=()=>history(true).catch(e=>message(e.message));
$("file").onchange=()=>{const file=$("file").files[0];if(file)$("fileInfo").textContent=`${file.name} · ${(file.size/1024**2).toFixed(1)} MiB`;};
const zone=$("dropzone");zone.ondragover=e=>{e.preventDefault();zone.classList.add("over");};zone.ondragleave=()=>zone.classList.remove("over");zone.ondrop=e=>{e.preventDefault();zone.classList.remove("over");if(e.dataTransfer.files.length!==1){message("請一次選擇一個檔案。");return;}$("file").files=e.dataTransfer.files;$("file").onchange();};
async function init(){
  const config=await api("/api/session");csrf=config.csrf;
  const models=await api("/api/models");$("model").replaceChildren();
  for(const model of models){const option=document.createElement("option");option.value=model.name;option.textContent=`${model.name} · ${model.ready?"已準備":"未準備"}`;option.disabled=!model.ready;$("model").append(option);}
  modelsReady=models.some(m=>m.ready);if(models.find(m=>m.name==="small").ready)$("model").value="small";
  $("modelHelp").textContent=modelsReady?"small 為預設；medium／large-v3-turbo 請先以短片段比較品質、耗時與記憶體。":"尚無可用模型。請先依 README 執行 prepare_models.py，再重新整理頁面。";
  await history();const latest=await api("/api/jobs/latest");if(latest)await selectJob(latest.job_id);updateButtons();
  setInterval(refreshActive,2000);
}
init().catch(e=>message(e.message));
