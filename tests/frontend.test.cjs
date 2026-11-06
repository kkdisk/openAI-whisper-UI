"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup({stored=null,blocked=false}={}){
  const elements = new Map();
  const values = new Map(stored===null?[]:[["local-transcript.workspace-cleared",stored]]);
  const node = () => ({disabled:false,children:[],replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},removeAttribute(){},focus(){this.focused=true;}});
  const exports = [{format:"md"},{format:"txt"},{format:"txt",variant:"raw"},{format:"srt"}].map(dataset=>Object.assign(node(),{dataset}));
  const document = {
    getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id);},
    querySelectorAll(){return exports;},
    createElement:node,
  };
  // Bootstrap remains pending; tests drive the actual UI functions with controlled responses.
  const context = vm.createContext({document, fetch:()=>new Promise(()=>{}), setTimeout, setInterval:()=>0});
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
