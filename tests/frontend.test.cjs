"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup(){
  const elements = new Map();
  const node = () => ({disabled:false, replaceChildren(){}, append(){}, removeAttribute(){}});
  const document = {
    getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id);},
    querySelectorAll(){return [];},
    createElement:node,
  };
  // Bootstrap remains pending; tests drive the actual UI functions with controlled responses.
  const context = vm.createContext({document, fetch:()=>new Promise(()=>{}), setTimeout, setInterval});
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../app/static/app.js"),"utf8"),context);
  const evaluate = code => vm.runInContext(code,context);
  return {elements, evaluate, context};
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
