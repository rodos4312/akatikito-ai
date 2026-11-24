// Run UI behavior without a browser or access to the real clipboard.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
class Element {
  constructor(tag='DIV') { this.tagName=tag.toUpperCase(); this.children=[]; this.style={}; this.dataset={}; this._value=''; this.type='text'; this.selectionStart=0; this.selectionEnd=0; this.className=''; this.textContent=''; this.scrollHeight=50; this.classList={toggle(){},add(){}}; }
  get options(){return this.children;}
  get value(){ return this._value; }
  set value(v){ this._value=String(v); }
  set innerHTML(v){this._html=v;this.children=[];}
  get innerHTML(){return this._html||'';}
  appendChild(c){c.parent=this;this.children.push(c); if(this.tagName==='SELECT' && this.children.length===1)this.value=c.value;return c;}
  append(...cs){cs.forEach(c=>this.appendChild(c));}
  prepend(c){c.parent=this;this.children.unshift(c);}
  replaceChildren(...cs){this.children=[];this._value='';this.append(...cs);}
  querySelectorAll(sel){ const all=this.children.flatMap(c=>[c,...c.querySelectorAll(sel)]); return all.filter(c=>sel==='.msg'?c.className.split(' ').includes('msg'):sel==='.status'?c.className==='status':false); }
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
  focus(){document.activeElement=this;}
  dispatchEvent(){}
  setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);this.selectionStart=this.selectionEnd=start+text.length;}
}
const ids = new Map(); const listeners={};
const document = {activeElement:null, getElementById(id){if(!ids.has(id))ids.set(id,new Element(['model','model-pick','provider','mode','backups'].includes(id)?'SELECT':id==='prompt'?'TEXTAREA':'DIV'));return ids.get(id);},createElement(tag){return new Element(tag);},addEventListener(name,fn){listeners[name]=fn;},querySelectorAll(sel){return document.getElementById('chat').querySelectorAll(sel);}};
let clipboard='';let sessionsReads=0;let status={connected:true,busy:false,provider:'DeepSeek',model:'custom/last-model',mode:'code',auto_mode:true,tokens:{},transcript:[],events:[],session_id:'a',project:'D:\project'};
const providers={providers:['DeepSeek','NanoGPT'],current:'DeepSeek',model:'custom/last-model',models:{DeepSeek:'custom/last-model',NanoGPT:'nano/saved'},project:'D:\project',suggested:{DeepSeek:['deepseek-chat'],NanoGPT:['nano/default']},keys:{}};
let copyFailure=false; let sendResult={ok:true};
const context={document,window:{getSelection:()=>({toString:()=> 'επιλεγμένη απάντηση'})},Event:class{},Option:class extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,confirm:()=>true,console,
  fetch:async(url,opt)=>({json:async()=>{
    const body=opt&&opt.body?JSON.parse(opt.body):{};
    if(url==='/api/providers')return providers;
    if(url==='/api/send')return sendResult;
    if(url==='/api/paste')return {text:clipboard};
    if(url==='/api/copy'){if(copyFailure)return {error:'blocked'};clipboard=body.text;return {ok:true};}
    if(url==='/api/models')return {models:['new/model'],error:''};
    if(url==='/api/sessions'){sessionsReads++;return {sessions:[{id:'a',title:'παλιά',project:'D:\project',project_name:'project'}]};}
    if(url==='/api/session/new')return {error:'busy'};
    if(url==='/api/poll'||url==='/api/status')return status;
    throw new Error('Unexpected URL '+url);
  }})};
vm.createContext(context);
let script=['chat.js','files.js','learning.js','settings.js'].map(f=>fs.readFileSync(path.join(__dirname,'../akatikito/ui',f),'utf8')).join('\n');
script=script.slice(0,script.lastIndexOf("init().catch"));
// No browser globals, network services or external packages are required.
const setupScript=path.join(__dirname,'../akatikito/onboarding.js');
if(fs.existsSync(setupScript))vm.runInContext(fs.readFileSync(setupScript,'utf8'),context);
vm.runInContext(script,context);
const run=source=>vm.runInContext(source,context);
(async()=>{
  await run('loadProviders()');
  assert.equal(document.getElementById('model-pick').value,'custom/last-model');
  await run('loadModels()');
  assert.equal(document.getElementById('model-pick').value,'custom/last-model');
  document.getElementById('provider').value='NanoGPT';run('onProvider()');
  assert.equal(document.getElementById('model-pick').value,'nano/saved');
  document.getElementById('provider').value='DeepSeek';run('onProvider()');
  assert.equal(document.getElementById('model-pick').value,'custom/last-model');
  console.log('OK saved custom model survives startup, list refresh and provider switches');
  const prompt=document.getElementById('prompt');prompt.value='αβγ';prompt.selectionStart=1;prompt.selectionEnd=2;prompt.focus();clipboard='\nΕλληνικά\n';
  let prevented=false;listeners.keydown({ctrlKey:true,key:'v',preventDefault(){prevented=true;}});
  await new Promise(resolve=>setImmediate(resolve));
  assert(prevented);assert.equal(prompt.value,'α\nΕλληνικά\nγ');assert.equal(prompt.selectionStart,11);
  prompt.selectionStart=2;prompt.selectionEnd=10;
  listeners.keydown({ctrlKey:true,key:'c',preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(clipboard,'Ελληνικά');
  document.activeElement=null;listeners.keydown({ctrlKey:true,key:'c',preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(clipboard,'επιλεγμένη απάντηση');
  console.log('OK clipboard shortcuts insert at cursor, preserve text and copy selected chat/input');
  copyFailure=true;await run('copyText("test")');copyFailure=false;
  assert.match(document.getElementById('app-notice').children[0].children[0].textContent,/blocked/);
  run('showNotice("err", "apostrophe \' and <markup>")');
  assert.equal(document.getElementById('app-notice').children[0].children[0].textContent,"apostrophe ' and <markup>");
  console.log('OK clipboard errors visible; notices handle quotes and markup as text');
  run('updateBadges({connected:true,busy:true,model:"working/model",provider:"Provider",mode:"debug",tokens:{}})');
  assert.match(document.getElementById('model-badge').textContent,/Δουλεύει: Provider · working\/model/);
  assert.equal(document.getElementById('model').value,'working/model');assert(document.getElementById('model').disabled);
  run('chat.replaceChildren(); renderTranscript([{role:"user",text:"ίδιο"},{role:"assistant",text:"ok",model:"first"},{role:"user",text:"ίδιο"},{role:"assistant",text:"ok",model:"second"}]);');
  assert.equal(document.getElementById('chat').querySelectorAll('.msg').length,4);
  assert.equal(document.getElementById('chat').children[3].children[0].textContent,'second');
  const count=document.getElementById('chat').children.length;await run('newSession()');assert.equal(document.getElementById('chat').children.length,count);
  console.log('OK working model display, repeated messages, per-answer models and failed session switches');
  status.events=[{kind:'done',data:{}}];await run('poll()');assert.equal(sessionsReads,1);
  console.log('OK session list refreshes when request completes');
  status.events=[];status.last_seq=101;status.transcript=[{role:'user',text:'upgrade task'}];
  run('connected=true;busy=false;');
  document.getElementById('prompt').value='upgrade task';
  sendResult={ok:true,conversation_changed:true,last_seq:100};
  await run('send()');
  const routedMessages=document.getElementById('chat').querySelectorAll('.msg');
  assert.equal(routedMessages.length,1);assert.equal(routedMessages[0].dataset.key,'user|upgrade task');
  assert.equal(run('lastSeq'),101);
  console.log('OK self-upgrade routing replaces old project chat and keeps new event cursor');
  sendResult={error:'read-only selection'};document.getElementById('prompt').value='blocked task';
  await run('send()');
  assert.equal(document.getElementById('prompt').value,'blocked task');
  assert.equal(document.getElementById('chat').querySelectorAll('.msg').length,1);
  console.log('OK rejected upgrade preserves previous chat and draft');
  run('updateBadges({connected:true,busy:false,model:"m",provider:"p",mode:"code",tokens:{},context:{sent_chars:1000,reduction_percent:75},turn_usage:{prompt_tokens:100,completion_tokens:20,model_calls:2,recall_calls:1}})');
  assert.match(document.getElementById('context-hint').textContent,/100 in \/ 20 out/);
  assert.match(document.getElementById('context-hint').textContent,/2 κλήσεις · 1 ανακτήσεις/);
  assert(!document.getElementById('context-hint').textContent.includes('75%'));
  console.log('OK request meter shows provider tokens and call counts instead of misleading compression percentages');
  console.log('UI BEHAVIOR OK');
})().catch(err=>{console.error(err);process.exitCode=1;});
