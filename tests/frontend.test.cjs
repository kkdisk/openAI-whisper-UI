"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup({stored=null,blocked=false}={}){
  const elements = new Map();
  const values = new Map(stored===null?[]:[["local-transcript.workspace-cleared",stored]]);
  const node = () => ({disabled:false,dataset:{},children:[],replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},insertBefore(item,next){this.children.splice(this.children.indexOf(next),0,item);},setAttribute(){},removeAttribute(){},focus(){this.focused=true;}});
  const exports = [{format:"md"},{format:"txt"},{format:"txt",variant:"raw"},{format:"srt"}].map(dataset=>Object.assign(node(),{dataset}));
  const document = {
    getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id);},
    querySelectorAll(){return exports;},
    createElement:node,
  };
  // Bootstrap remains pending; tests drive the actual UI functions with controlled responses.
  const context = vm.createContext({document, fetch:()=>new Promise(()=>{}), setTimeout, clearTimeout, setInterval:()=>0});
  Object.defineProperty(context,"sessionStorage",{get(){
    if(blocked)throw new Error("storage blocked");
    return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../app/static/app.js"),"utf8"),context);
  const evaluate = code => vm.runInContext(code,context);
  return {elements, evaluate, context, values, exports};
}

test("viewing history still releases the completed background slot without replacing preview",async()=>{
  const {elements,evaluate} = setup();
  await evaluate(`
    modelsReady=true;active="A";
    var closed=false;
    stream={close(){closed=true;}};
    var completed={job_id:"B",status:"completed",request:{title:"History B"},
      duration_sec:1,last_position_sec:1,elapsed_sec:1,warnings:[]};
    api=async path=>path.endsWith("/segments?after_id=0")?[]:completed;
    selectJob("B");
  `);
  assert.equal(evaluate("closed"),true);
  assert.equal(elements.get("start").disabled,true);
  await evaluate(`
    api=async()=>({active_id:null,items:[],total:0});
    refreshActive();
  `);
  assert.equal(elements.get("start").disabled,false);
  assert.equal(evaluate("selected.job_id"),"B");
  assert.equal(elements.get("jobTitle").textContent,"History B");
});

test("stale activity response cannot replace a newer task",async()=>{
  const {evaluate} = setup();
  evaluate(`
    modelsReady=true;active="A";
    var release;
    api=()=>new Promise(resolve=>{release=resolve;});
    var waiting=refreshActive();
    active="C";
    release({active_id:null});
  `);
  await evaluate("waiting");
  assert.equal(evaluate("active"),"C");
});

test("temporary network failure keeps the slot and permits another poll",async()=>{
  const {evaluate} = setup();
  await evaluate(`
    active="A";api=async()=>{throw new Error("offline");};refreshActive();
  `);
  assert.equal(evaluate("active"),"A");
  assert.equal(evaluate("activePollPending"),false);
  await evaluate(`api=async()=>({active_id:null,items:[],total:0});refreshActive();`);
  assert.equal(evaluate("active"),null);
});

test("new transcription sends mixed language and context selection",async()=>{
  const {elements,evaluate} = setup();
  evaluate(`
    $("file").files=[{name:"meeting.wav",size:3}];
    $("language").value="mixed";$("previousContext").checked=false;
    $("threads").value="8";$("model").value="large-v3-turbo";
    $("device").value="cuda";$("computeType").value="float16";
    $("deviceIndex").value="1";
    var submitted;
    api=async(path,options)=>{
      if(options?.method==="POST"){
        submitted=JSON.parse(options.body);
        throw new Error("Stop before upload in this isolated test");
      }
      return {active_id:null,items:[],total:0};
    };
  `);
  await elements.get("form").onsubmit({preventDefault(){}});
  assert.equal(evaluate("submitted.language"),"mixed");
  assert.equal(evaluate("submitted.condition_on_previous_text"),false);
  assert.equal(evaluate("submitted.model"),"large-v3-turbo");
  assert.equal(evaluate("submitted.device"),"cuda");
  assert.equal(evaluate("submitted.compute_type"),"float16");
  assert.equal(evaluate("submitted.device_index"),1);
});

function seedCompleted(evaluate,status="completed"){
  evaluate(`
    var saved={job_id:"A",status:${JSON.stringify(status)},request:{title:"Meeting A"},
      duration_sec:100,last_position_sec:90,elapsed_sec:30,warnings:[]};
    modelsReady=true;showMeta(saved);
    append({segment_id:1,start_sec:0,end_sec:1,text:"API GLT-123"});
  `);
}

for(const status of ["completed","failed","cancelled","interrupted"]){
  test(`clearing ${status} resets only the workspace without backend mutations`,()=>{
    const {elements,evaluate,values,exports}=setup();seedCompleted(evaluate,status);
    evaluate(`
      $("file").files=[{name:"keep.wav"}];$("title").value="Keep title";
      $("terms").value="GLT-123";message("Old warning");
      pendingRows.set(99,{segment_id:99,text:"pending"});
      var closed=0,requests=[];stream={close(){closed++;}};
      api=(...args)=>{requests.push(args);throw new Error("clear must not call APIs");};
    `);
    assert.equal(elements.get("clearWorkspace").disabled,false);
    assert.equal(evaluate("clearWorkspace()"),true);
    assert.equal(evaluate("selected"),null);assert.equal(evaluate("active"),null);
    assert.equal(evaluate("rows.length+pendingRows.size+lastId"),0);
    assert.equal(evaluate("closed"),1);assert.equal(evaluate("stream"),null);
    assert.equal(evaluate("requests.length"),0);
    assert.equal(elements.get("jobTitle").textContent,"逐字稿");
    assert.equal(elements.get("progress").value,0);
    assert.equal(elements.get("message").textContent,"");
    assert.match(elements.get("status").textContent,/歷史/);
    assert.equal(elements.get("copy").disabled,true);
    assert(exports.every(button=>button.disabled));
    assert.equal(elements.get("clearWorkspace").disabled,true);
    assert.equal(elements.get("transcript").focused,true);
    assert.equal(elements.get("file").files[0].name,"keep.wav");
    assert.equal(elements.get("title").value,"Keep title");
    assert.equal(elements.get("terms").value,"GLT-123");
    assert.equal(values.get("local-transcript.workspace-cleared"),"1");
  });
}

test("empty, active, uploading and submission states cannot be cleared",()=>{
  const {elements,evaluate}=setup();
  evaluate("updateButtons()");assert.equal(elements.get("clearWorkspace").disabled,true);
  assert.equal(evaluate("clearWorkspace()"),false);
  for(const status of ["uploading","validating","loading_model","decoding","transcribing","cancelling"]){
    evaluate("active=null");seedCompleted(evaluate,status);
    assert.equal(elements.get("clearWorkspace").disabled,true);
    assert.equal(evaluate("clearWorkspace()"),false);
    assert.equal(evaluate("selected.status"),status);
  }
  evaluate("active=null");seedCompleted(evaluate);
  for(const condition of ["active='other'","submitting=true","xhr={}"]){
    evaluate(`active=null;submitting=false;xhr=null;${condition};updateButtons()`);
    assert.equal(elements.get("clearWorkspace").disabled,true);
    assert.equal(evaluate("clearWorkspace()"),false);
  }
});

test("a delayed segment page cannot fill a cleared workspace",async()=>{
  const {evaluate}=setup();seedCompleted(evaluate);
  evaluate(`api=async path=>path.includes('/segments')?new Promise(resolve=>{globalThis.releasePage=resolve;}):saved;
    var loading=selectJob('A');`);
  await evaluate("Promise.resolve()");
  assert.equal(evaluate("typeof releasePage"),"function");
  assert.equal(evaluate("clearWorkspace()"),true);
  evaluate(`releasePage([{segment_id:1,start_sec:0,end_sec:1,text:'late'}])`);
  await evaluate("loading");
  assert.equal(evaluate("selected"),null);assert.equal(evaluate("rows.length"),0);
});

test("late SSE status, segment and terminal events are ignored after clear",async()=>{
  const {evaluate,elements}=setup();
  evaluate(`
    EventSource=class {constructor(){this.handlers={};}addEventListener(name,fn){this.handlers[name]=fn;}close(){this.closed=true;}};
    var running={job_id:'A',status:'transcribing',request:{title:'A'},duration_sec:100,last_position_sec:1,elapsed_sec:1,warnings:[]};
    api=async path=>path.includes('/segments')?[]:running;
  `);
  await evaluate("selectJob('A')");
  evaluate(`var oldStream=stream;oldStream.handlers.status({data:JSON.stringify({...running,status:'completed'})});`);
  assert.equal(evaluate("clearWorkspace()"),true);
  evaluate(`
    oldStream.handlers.status({data:JSON.stringify(running)});
    oldStream.handlers.segment({data:JSON.stringify({segment_id:1,text:'late'})});
    oldStream.handlers.done({data:'{}'});oldStream.onerror({});
  `);
  assert.equal(evaluate("selected"),null);assert.equal(evaluate("active"),null);
  assert.equal(evaluate("rows.length"),0);assert.equal(elements.get("message").textContent,"");
});

test("reload keeps empty intent and opening history restores the saved job",async()=>{
  const {evaluate,values,exports}=setup({stored:"1"});
  evaluate(`api=async()=>{throw new Error('must not fetch latest while cleared');}`);
  await evaluate("restoreWorkspace()");assert.equal(evaluate("selected"),null);
  seedCompleted(evaluate);
  evaluate(`resetWorkspace({cleared:true});api=async path=>path.includes('/segments')?[{segment_id:1,start_sec:0,end_sec:1,text:'saved'}]:saved;`);
  await evaluate("selectJob('A')");
  assert.equal(evaluate("selected.job_id"),"A");assert.equal(evaluate("rows.length"),1);
  assert.equal(values.has("local-transcript.workspace-cleared"),false);
  assert(exports.every(button=>!button.disabled));
});

test("delayed latest result cannot override a manual clear",async()=>{
  const {evaluate}=setup();
  evaluate(`api=()=>new Promise(resolve=>{globalThis.releaseLatest=resolve;});var restoring=restoreWorkspace();`);
  seedCompleted(evaluate);assert.equal(evaluate("clearWorkspace()"),true);
  evaluate("releaseLatest(saved)");await evaluate("restoring");
  assert.equal(evaluate("selected"),null);assert.equal(evaluate("workspaceCleared"),true);
});

test("a delayed bootstrap failure is ignored after manual clear",async()=>{
  const {evaluate,elements}=setup();
  evaluate(`api=()=>new Promise((resolve,reject)=>{globalThis.rejectLatest=reject;});var restoring=restoreWorkspace();`);
  seedCompleted(evaluate);assert.equal(evaluate("clearWorkspace()"),true);
  evaluate("rejectLatest(new Error('stale bootstrap error'))");await evaluate("restoring");
  assert.equal(elements.get("message").textContent,"");
});

test("blocked storage still clears the page and a new load uses normal restoration",async()=>{
  const first=setup({blocked:true});seedCompleted(first.evaluate);
  assert.equal(first.evaluate("clearWorkspace()"),true);
  assert.equal(first.evaluate("workspaceCleared"),true);
  const reload=setup({blocked:true});seedCompleted(reload.evaluate);
  reload.evaluate(`resetWorkspace();api=async path=>path==='/api/jobs/latest'?{job_id:'A'}:path.includes('/segments')?[]:saved;`);
  await reload.evaluate("restoreWorkspace()");
  assert.equal(reload.evaluate("selected.job_id"),"A");
});

test("clearing is blocked while a new job creation request is pending",async()=>{
  const {elements,evaluate}=setup();seedCompleted(evaluate);
  evaluate(`
    $("file").files=[{name:'next.wav',size:3}];
    api=(path,options)=>options?.method==='POST'?new Promise((resolve,reject)=>{globalThis.rejectCreate=reject;}):Promise.resolve({active_id:null,items:[],total:0});
    var posting=$("form").onsubmit({preventDefault(){}});
  `);
  assert.equal(elements.get("clearWorkspace").disabled,true);
  assert.equal(evaluate("clearWorkspace()"),false);
  evaluate("rejectCreate(new Error('test stopped before upload'))");await evaluate("posting");
  assert.equal(evaluate("submitting"),false);
  assert.equal(elements.get("clearWorkspace").disabled,false);
});

test("late cancellation response cannot restore a cleared job",async()=>{
  const {evaluate}=setup();seedCompleted(evaluate,"transcribing");
  evaluate(`api=()=>new Promise(resolve=>{globalThis.finishCancel=resolve;});var cancelling=$("cancel").onclick();showMeta({...saved,status:'completed'});`);
  assert.equal(evaluate("clearWorkspace()"),true);
  evaluate("finishCancel({...saved,status:'cancelling'})");await evaluate("cancelling");
  assert.equal(evaluate("selected"),null);assert.equal(evaluate("active"),null);
});

for(const reject of [false,true]){
  test(`late clipboard ${reject?'failure':'success'} cannot write to a cleared view`,async()=>{
    const {evaluate,elements,context}=setup();seedCompleted(evaluate);
    let finish;
    context.navigator={clipboard:{writeText:()=>new Promise((resolve,rejectFn)=>{finish=reject?rejectFn:resolve;})}};
    const writing=elements.get("copy").onclick();
    assert.equal(evaluate("clearWorkspace()"),true);finish();await writing;
    assert.equal(elements.get("message").textContent,"");
  });
}

test("late export error does not refill the cleared warning area",async()=>{
  const {evaluate,elements,exports,context}=setup();seedCompleted(evaluate);
  let finish;
  context.fetch=()=>new Promise(resolve=>{finish=resolve;});
  const downloading=exports[0].onclick();
  assert.equal(evaluate("clearWorkspace()"),true);
  finish({ok:false,json:async()=>({message:'late export error'})});await downloading;
  assert.equal(elements.get("message").textContent,"");
});

async function seedEditor(evaluate,status="transcribing",savedRows=[]){
  await evaluate(`
    var meta={job_id:"A",status:${JSON.stringify(status)},request:{title:"Meeting A"},duration_sec:100,last_position_sec:10,elapsed_sec:10,warnings:[]};
    var calls=[];
    api=async(path,options)=>{
      calls.push({path,options});
      if(path.endsWith("/revisions"))return {version:0,rows:${JSON.stringify(savedRows)}};
      if(path.includes("/segments?"))return [{segment_id:1,start_sec:0,end_sec:5,text:"original"}];
      if(path==="/api/jobs?offset=0&limit=50")return {items:[],total:0,active_id:null};
      return meta;
    };
    var handlers={};EventSource=class{addEventListener(name,fn){handlers[name]=fn;}close(){}};
    selectJob("A");
  `);
}

test("stream completion and appended segments preserve the active edit control",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate);
  const input=elements.get("transcript").children[0].children[1].children[0];
  input.value="corrected";input.oninput();
  evaluate(`handlers.segment({data:JSON.stringify({segment_id:2,start_sec:5,end_sec:10,text:"later"})});`);
  await evaluate(`meta={...meta,status:"completed"};handlers.done({data:"{}"});`);
  assert.equal(elements.get("transcript").children[0].children[1].children[0],input);
  assert.equal(input.value,"corrected");
  assert.equal(evaluate("rows.length"),2);
  assert.equal(evaluate("editor.dirty.get('s:1').text"),"corrected");
  evaluate("clearTimeout(editor.timer)");
});

test("edits made during a save are serialized with the returned version",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate);
  const input=elements.get("transcript").children[0].children[1].children[0];
  input.value="first";input.oninput();
  evaluate(`var release;var bodies=[];api=async(path,options)=>{
    bodies.push(JSON.parse(options.body));
    if(bodies.length===1)return new Promise(resolve=>{release=resolve;});
    return {version:2,rows:[]};
  };var writing=flushEdits();`);
  input.value="second";input.oninput();
  evaluate(`release({version:1,rows:[]});`);
  assert.equal(await evaluate("writing"),true);
  assert.equal(evaluate("bodies[0].row.text"),"first");
  assert.equal(evaluate("bodies[1].row.text"),"second");
  assert.equal(evaluate("bodies[1].version"),1);
  assert.equal(evaluate("editor.dirty.size"),0);
  evaluate("clearTimeout(editor.timer)");
});

test("save failure blocks navigation and clearing until successful retry",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate,"completed");
  const input=elements.get("transcript").children[0].children[1].children[0];
  input.value="keep me";input.oninput();
  evaluate(`api=async()=>{throw new Error("disk full");};`);
  assert.equal(await evaluate("selectJob('B')"),false);
  assert.equal(evaluate("selected.job_id"),"A");
  assert.equal(evaluate("clearWorkspace()"),false);
  assert.equal(input.value,"keep me");
  assert.match(elements.get("saveStatus").textContent,/disk full/);
  await evaluate(`api=async()=>({version:1,rows:[]});flushEdits();`);
  assert.equal(evaluate("editor.dirty.size"),0);
  assert.equal(evaluate("clearWorkspace()"),true);
});

test("restored edits, inserted rows and deletion agree with copied content",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate,"completed",[
    {key:"u:abc",anchor:0,text:"intro",deleted:false,start_sec:null,end_sec:null},
    {key:"s:1",anchor:0,text:"corrected",deleted:true},
  ]);
  assert.equal(elements.get("transcript").children.length,2);
  assert.equal(evaluate("editedRows().length"),1);
  assert.equal(evaluate("editedRows()[0].text"),"intro");
  const source=elements.get("transcript").children[1].children[1];
  assert.equal(source.children[0].disabled,true);
  source.children[1].children[0].onclick();
  assert.equal(evaluate("editedRows()[1].text"),"corrected");
  source.children[1].children[1].onclick();
  assert.equal(evaluate("editedRows()[1].text"),"original");
  evaluate("clearTimeout(editor.timer)");
});

test("inserting before an existing segment retains source order and later appends",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate,"completed");
  await evaluate(`api=async(path,options)=>({version:1,rows:[{...JSON.parse(options.body).row,key:"u:new"}]});insertSentence(0);`);
  assert.equal(elements.get("transcript").children[0].dataset.key,"u:new");
  assert.equal(evaluate("editedRows().length"),2);
  evaluate(`append({segment_id:2,start_sec:5,end_sec:10,text:"later"});`);
  assert.equal(elements.get("transcript").children.length,3);
});

test("revision load failure blocks corrected export but still permits the raw backup",async()=>{
  const {evaluate,exports,context}=setup();
  await evaluate(`
    api=async path=>{
      if(path.endsWith("/revisions"))throw new Error("revision corrupt");
      return {job_id:"A",status:"completed",request:{title:"A"},warnings:[]};
    };selectJob("A");
  `);
  let fetched=[];
  context.fetch=async path=>{fetched.push(path);return {ok:false,json:async()=>({message:"test stops before download"})};};
  await exports[0].onclick();
  assert.equal(fetched.length,0);
  await exports[2].onclick();
  assert.equal(fetched.length,1);
  assert.match(fetched[0],/variant=raw/);
});

test("raw backup bypasses failed or pending revision writes and retains local edits",async()=>{
  const {evaluate,exports,context}=setup();await seedEditor(evaluate,"completed");
  evaluate(`
    editor.dirty.set("s:1",{key:"s:1",text:"keep local edit"});
    api=async()=>{throw new Error("revision conflict");};
  `);
  const fetched=[];
  context.fetch=async path=>{fetched.push(path);return {ok:false,json:async()=>({message:"test stops before download"})};};
  await exports[0].onclick();
  assert.equal(fetched.length,0);
  assert.match(evaluate("editor.error"),/revision conflict/);
  await exports[2].onclick();
  assert.equal(fetched.length,1);
  assert.match(fetched[0],/variant=raw/);
  evaluate(`editor.saving=new Promise(()=>{});editor.inserting=true;`);
  await exports[2].onclick();
  assert.equal(fetched.length,2);
  assert.equal(evaluate("editor.dirty.get('s:1').text"),"keep local edit");
  assert.match(evaluate("editor.error"),/revision conflict/);
});

test("pause waits for acknowledgement, resumes separately and does not wait for edit saves",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate);
  evaluate(`
    editor.dirty.set("s:1",{key:"s:1",text:"unsaved"});
    var controlCalls=[];
    api=async(path,options)=>{
      controlCalls.push(path);
      return {...selected,control_seq:1,execution_state:"pause_requested"};
    };
  `);
  await elements.get("pauseResume").onclick();
  assert.equal(evaluate("controlCalls[0]"),"/api/jobs/A/pause");
  assert.equal(evaluate("controlCalls.length"),1);
  assert.equal(elements.get("pauseResume").disabled,true);
  evaluate(`showMeta({...selected,execution_state:"paused",paused_sec:20,active_elapsed_sec:10});`);
  assert.equal(elements.get("pauseResume").textContent,"繼續轉錄");
  assert.equal(elements.get("pauseResume").disabled,false);
  assert.equal(elements.get("cancel").disabled,false);
  assert.match(elements.get("status").textContent,/已暫停/);
  assert.match(elements.get("timing").textContent,/暫停估算/);
  await evaluate(`api=async(path)=>{controlCalls.push(path);return {...selected,control_seq:2,execution_state:"resume_requested"};};$("pauseResume").onclick();`);
  assert.equal(evaluate("controlCalls[1]"),"/api/jobs/A/resume");
  assert.equal(evaluate("editor.dirty.size"),1);
});

test("late HTTP and SSE snapshots cannot regress pause acknowledgements or terminal state",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate);
  evaluate(`var release;api=()=>new Promise(resolve=>{release=resolve;});var requestControl=$("pauseResume").onclick();`);
  evaluate(`showMeta({...selected,control_seq:1,execution_state:"paused"});release({...selected,execution_state:"pause_requested"});`);
  await evaluate("requestControl");
  assert.equal(evaluate("selected.execution_state"),"paused");
  evaluate(`showMeta({...selected,control_seq:0,execution_state:"running"});`);
  assert.equal(evaluate("selected.execution_state"),"paused");
  evaluate(`showMeta({...selected,status:"completed"});showMeta({...selected,status:"transcribing",execution_state:"pause_requested"});`);
  assert.equal(evaluate("selected.status"),"completed");
  assert.equal(elements.get("pauseResume").disabled,true);
});

test("pause restores on history load and is disabled outside transcription",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate,"completed");
  for(const status of ["uploading","loading_model","decoding","cancelling","completed"]){
    evaluate(`selected=null;showMeta({...meta,status:${JSON.stringify(status)}});`);
    assert.equal(elements.get("pauseResume").disabled,true);
  }
  evaluate(`selected=null;showMeta({...meta,status:"transcribing",execution_state:"paused",control_seq:1});`);
  assert.equal(elements.get("pauseResume").textContent,"繼續轉錄");
  assert.equal(elements.get("pauseResume").disabled,false);
  evaluate(`showMeta({...selected,control_seq:2,execution_state:"resume_requested",control_wait_sec:16});`);
  assert.match(elements.get("pauseHint").textContent,/仍在等待/);
});

test("continue is offered only for unfinished terminal jobs with saved segments",async()=>{
  const {evaluate,elements}=setup();await seedEditor(evaluate,"cancelled");
  const show=patch=>evaluate(`selected=null;active=null;showMeta({...meta,segment_count:3,...${JSON.stringify(patch)}});`);
  show({});
  assert.equal(elements.get("continueJob").hidden,false);
  assert.equal(elements.get("continueJob").disabled,false);
  assert.match(elements.get("pauseHint").textContent,/續跑/);
  for(const patch of [{status:"completed"},{segment_count:0},{status:"transcribing"}]){
    show(patch);assert.equal(elements.get("continueJob").disabled,true,JSON.stringify(patch));
  }
  show({last_position_sec:99.8});
  assert.equal(elements.get("continueJob").disabled,true);
  show({status:"interrupted"});
  evaluate(`active="B";updateButtons();`);
  assert.equal(elements.get("continueJob").disabled,true);
});

test("continue checks size, posts current settings and uploads to the same job",async()=>{
  const {evaluate,elements,context}=setup();await seedEditor(evaluate,"cancelled");
  evaluate(`selected=null;active=null;meta={...meta,segment_count:3,request:{title:"Meeting A",source_name:"a.m4a",expected_bytes:5,model:"small",cpu_threads:4}};showMeta(meta);
    $("model").value="medium";$("threads").value="8";$("device").value="cpu";
    var uploads=[];uploadMedia=async(id,file)=>{uploads.push([id,file.size]);};`);
  let confirms=0;context.confirm=()=>{confirms++;return true;};
  elements.get("continueFile").files=[{size:4}];
  await elements.get("continueFile").onchange();
  assert.match(elements.get("message").textContent,/大小與原任務不同/);
  assert.equal(confirms,0);
  assert.equal(evaluate("calls.some(c=>c.path.endsWith('/continue'))"),false);
  evaluate(`const original=api;api=async(path,options)=>{if(path.endsWith("/continue")){calls.push({path,options});return {...meta,status:"uploading"};}return original(path,options);};`);
  elements.get("continueFile").files=[{size:5}];
  await elements.get("continueFile").onchange();
  assert.equal(confirms,1);
  const body=JSON.parse(evaluate("calls.find(c=>c.path.endsWith('/continue')).options.body"));
  assert.deepEqual(body,{expected_bytes:5,model:"medium",cpu_threads:8,device:"cpu",compute_type:"auto",device_index:0});
  assert.equal(evaluate("JSON.stringify(uploads)"),JSON.stringify([["A",5]]));
  assert.equal(evaluate("submitting"),false);
});

test("declining the continue confirmation sends nothing",async()=>{
  const {evaluate,elements,context}=setup();await seedEditor(evaluate,"failed");
  evaluate(`selected=null;active=null;meta={...meta,segment_count:1,request:{title:"A",source_name:"a.m4a",expected_bytes:5}};showMeta(meta);`);
  context.confirm=()=>false;
  elements.get("continueFile").files=[{size:5}];
  await elements.get("continueFile").onchange();
  assert.equal(evaluate("calls.some(c=>c.path.endsWith('/continue'))"),false);
  assert.equal(evaluate("submitting"),false);
});

test("remaining time after a continuation uses only this run's progress",()=>{
  const {evaluate,elements}=setup();
  evaluate(`showMeta({job_id:"A",status:"transcribing",execution_state:"running",request:{title:"A"},duration_sec:8000,last_position_sec:2200,active_elapsed_sec:600,elapsed_sec:600,warnings:[],continuations:[{from_sec:2000}]});`);
  // 本次 600 秒處理 200 秒 → 剩 5800 秒需 17400 秒
  assert.match(elements.get("timing").textContent,/剩餘 04:50:00/);
  assert.match(elements.get("timing").textContent,/本次續跑耗時/);
});

test("duration limit text follows the server setting",()=>{
  const {evaluate}=setup();
  assert.equal(evaluate("durationText(43200)"),"12 小時");
  assert.equal(evaluate("durationText(10800)"),"3 小時");
  assert.equal(evaluate("durationText(7200+60*30)"),"150 分鐘");
});
