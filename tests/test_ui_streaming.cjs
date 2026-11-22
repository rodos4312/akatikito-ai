const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
class Element {
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.style={};this.dataset={};this.className='';this.textContent='';this.value='';this.classList={add(){},toggle(){}};}
  get options(){return this.children;}
  appendChild(c){c.parent=this;this.children.push(c);return c;}
  append(...cs){cs.forEach(c=>this.appendChild(c));}
  prepend(c){c.parent=this;this.children.unshift(c);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
  set innerHTML(v){this._html=v;this.children=[];}
  get innerHTML(){return this._html||'';}
  replaceChildren(...cs){this.children=[];this.append(...cs);}
  querySelectorAll(selector){return this.children.flatMap(c=>[c,...c.querySelectorAll(selector)]).filter(c=>c.className.split(' ').includes(selector.slice(1)));}
}
const ids=new Map();
const document={getElementById(id){if(!ids.has(id))ids.set(id,new Element());return ids.get(id);},createElement(tag){return new Element(tag);},addEventListener(){},querySelectorAll(sel){return document.getElementById('chat').querySelectorAll(sel);}};
let sessions=[];
let state={busy:true,connected:true,model:'synthetic',mode:'code',tokens:{},events:[],transcript:[],last_seq:0};
const context={document,window:{},console,confirm:()=>true,setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,Event:class{},Option:class{},
  fetch:async(url)=>({json:async()=>url==='/api/sessions'?{sessions}:url==='/api/session/delete'?{ok:true}:state})};
vm.createContext(context);
let script=fs.readFileSync(path.join(__dirname,'../akatikito/ui.html'),'utf8').split('<script>')[1].split('</script>')[0];
script=script.slice(0,script.lastIndexOf('init().catch'));
vm.runInContext(script,context);
const run=source=>vm.runInContext(source,context);
const chat=document.getElementById('chat');
(async()=>{
  run('renderTranscript([{role:"user",text:"question"}]); streamEvent("stream_start", {stream_id:"a",model:"m"}); streamEvent("stream_delta",{stream_id:"a",text:"<script>unsafe</script> "});');
  assert.equal(run('liveStream.body.textContent'),'<script>unsafe</script> ');
  assert.equal(run('liveStream.body.innerHTML'),'');
  assert.equal(chat.querySelectorAll('.msg').length,1);
  run('streamEvent("stream_delta",{stream_id:"stale",text:"wrong"});');
  assert(!run('liveStream.text').includes('wrong'));
  run('streamEvent("stream_delta",{stream_id:"a",text:"done"}); streamEvent("stream_end",{stream_id:"a",status:"complete"});');
  state.busy=false;state.events=[];state.transcript=[{role:'user',text:'question'},{role:'assistant',text:'<script>unsafe</script> done',model:'m'}];
  await run('poll()');await run('poll()');
  assert.equal(chat.querySelectorAll('.msg').length,2);
  assert.equal(chat.querySelectorAll('.stream-preview').length,0);
  assert.equal(run('liveStream'),null);
  console.log('OK partial safe text before done, stale deltas ignored, final answer committed once');
  state.busy=true;state.stream={id:'reconnected',text:'missed earlier chunks',status:'active',model:'m'};
  await run('poll()');
  assert.equal(run('liveStream.body.textContent'),'missed earlier chunks');
  state.busy=false;state.stream.status='error';
  await run('poll()');await run('poll()');
  assert.equal(chat.querySelectorAll('.stream-preview').length,1);
  assert.match(run('liveStream.label.textContent'),/ατελές/);
  assert.equal(chat.querySelectorAll('.msg').length,2);
  console.log('OK reload/poll state restores partial output; interrupted text is never a completed answer');
  run('streamEvent("stream_start", {stream_id:"tools"}); streamEvent("stream_delta",{stream_id:"tools",text:"plan"}); streamEvent("stream_end",{stream_id:"tools",status:"tools"});');
  assert.equal(run('liveStream'),null);
  assert.equal(chat.querySelectorAll('.stream-preview').length,0);
  console.log('OK intermediate tool-call text does not duplicate a chat answer');
  sessions=[{id:'other',project:'D:/project',title:'other'}];
  run('currentSession="active";currentProject="D:/project";');
  await run('loadSessions()');
  const count=chat.querySelectorAll('.msg').length;
  await document.getElementById('sessions').children[0].children[1].onclick();
  assert.equal(chat.querySelectorAll('.msg').length,count);
  console.log('OK deleting a different saved session preserves the active chat and preview state');
})().catch(error=>{console.error(error);process.exitCode=1;});
