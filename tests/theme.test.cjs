"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,"../app/static/theme.js"),"utf8");

function setup({stored=null,dark=false,blocked=false}={}){
  const values=new Map(stored===null?[]:[["local-transcript.theme",stored]]);
  const listeners={},windowListeners={},controlListeners={};
  const dataset={};
  const control={value:"system",addEventListener:(event,callback)=>{controlListeners[event]=callback;}};
  const system={matches:dark,addEventListener:(event,callback)=>{listeners.system=callback;}};
  const document={documentElement:{dataset},readyState:"loading",getElementById:()=>control,
    addEventListener:(event,callback)=>{listeners[event]=callback;}};
  const window={matchMedia:()=>system,addEventListener:(event,callback)=>{windowListeners[event]=callback;}};
  Object.defineProperty(window,"localStorage",{get(){
    if(blocked)throw new Error("storage blocked");
    return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  }});
  vm.runInNewContext(source,{window,document});
  return {dataset,values,control,
    ready(){listeners.DOMContentLoaded();},
    choose(value){control.value=value;controlListeners.change();},
    systemDark(value){system.matches=value;listeners.system();},
    storage(key,newValue){windowListeners.storage({key,newValue});}};
}

test("saved preference applies before DOM ready and survives reload",()=>{
  const ui=setup({stored:"dark",dark:false});
  assert.equal(ui.dataset.theme,"dark");
  ui.ready();assert.equal(ui.control.value,"dark");
  ui.choose("light");assert.equal(ui.dataset.theme,"light");
  assert.equal(ui.values.get("local-transcript.theme"),"light");
  const reload=setup({stored:ui.values.get("local-transcript.theme"),dark:true});
  assert.equal(reload.dataset.theme,"light");
});

test("system mode follows live changes while explicit mode takes priority",()=>{
  const ui=setup({dark:true});ui.ready();
  assert.equal(ui.control.value,"system");assert.equal(ui.dataset.theme,"dark");
  ui.systemDark(false);assert.equal(ui.dataset.theme,"light");
  ui.choose("dark");ui.systemDark(false);assert.equal(ui.dataset.theme,"dark");
  ui.choose("system");assert.equal(ui.dataset.theme,"light");
  ui.systemDark(true);assert.equal(ui.dataset.theme,"dark");
});

test("blocked storage still permits changing display mode",()=>{
  const ui=setup({blocked:true,dark:true});ui.ready();
  assert.equal(ui.dataset.theme,"dark");
  ui.choose("light");assert.equal(ui.dataset.theme,"light");
});

test("invalid stored values fall back to system and cannot enter the DOM",()=>{
  const ui=setup({stored:'<script>alert(1)</script>',dark:true});ui.ready();
  assert.equal(ui.dataset.themePreference,"system");
  assert.equal(ui.dataset.theme,"dark");assert.equal(ui.control.value,"system");
});

test("other tabs update preference and clearing storage restores system mode",()=>{
  const ui=setup({stored:"dark",dark:true});ui.ready();
  ui.storage("unrelated","light");assert.equal(ui.dataset.theme,"dark");
  ui.storage("local-transcript.theme","light");assert.equal(ui.dataset.theme,"light");
  assert.equal(ui.control.value,"light");
  ui.storage(null,null);assert.equal(ui.dataset.theme,"dark");
  assert.equal(ui.control.value,"system");
});
