const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
class Element {
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.style={};this.dataset={};this.value='';this.textContent='';this.className='';this.hidden=false;this.selectionStart=0;this.selectionEnd=0;this.scrollHeight=50;this.classList={toggle(){},add(){}};}
  appendChild(c){this.children.push(c);c.parent=this;return c;}
  append(...cs){cs.forEach(c=>this.appendChild(c));}
  replaceChildren(...cs){this.children=[];this.append(...cs);}
  set innerHTML(v){this._html=v;this.children=[];}
  get innerHTML(){return this._html||'';}
  get options(){return this.children;}
  querySelectorAll(){return [];}
  setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);this.selectionStart=this.selectionEnd=start+text.length;}
  focus(){document.activeElement=this;}
}
const ids=new Map();
const document={getElementById(id){if(!ids.has(id))ids.set(id,new Element(id==='prompt'?'textarea':'div'));return ids.get(id);},createElement(tag){return new Element(tag);},addEventListener(){},querySelectorAll(){return [];}};
let calls=[], handler=async()=>{throw Error('Missing mock')};
const context={document,window:{},console,confirm:()=>true,setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,Option:class extends Element{},Event:class{},fetch:async(url,opt)=>{
  const body=opt&&opt.body?JSON.parse(opt.body):{};calls.push({url,body});return {json:()=>handler(url,body)};
}};
vm.createContext(context);
const html=fs.readFileSync(path.join(__dirname,'../akatikito/ui.html'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../akatikito/ui/style.css'),'utf8');
assert(html.includes('id="files-panel"') && html.includes('id="file-preview"') && css.includes('#files-panel[hidden]'));
let script=['chat.js','files.js','learning.js','settings.js'].map(f=>fs.readFileSync(path.join(__dirname,'../akatikito/ui',f),'utf8')).join('\n');script=script.slice(0,script.lastIndexOf('init().catch'));
vm.runInContext(script,context);const run=code=>vm.runInContext(code,context);
const sample=(extra={})=>({path:'src/a.py',text:'<script>never execute</script> Καλημέρα',project:'D:/project',session_id:'a',size:120,offset:0,next:20,total_chars:50,line_start:1,line_end:3,version:'v1',encoding:'utf-8',files:[{path:'src/a.py',action:'νέο'}],...extra});
const watchdog=setTimeout(()=>{console.error('File UI tests did not finish');process.exitCode=1;},10000);
(async()=>{
  run('updateFiles({project:"D:/project",session_id:"a",files:[]})');
  handler=async(url,body)=>{assert.equal(url,'/api/files/open');return sample();};
  await run('openProjectFile("src/a.py")');
  assert.equal(document.getElementById('file-text').textContent,sample().text);
  assert.equal(document.getElementById('file-text').innerHTML,'');
  assert.equal(document.getElementById('file-title').textContent,'src/a.py');
  assert.equal(document.getElementById('file-preview').hidden,false);
  assert.deepEqual(calls[0].body,{path:'src/a.py',offset:0,version:'',project:'D:/project',session_id:'a'});
  const rendered=document.getElementById('file-list').children[0];
  run('updateFiles({project:"D:/project",session_id:"a",files:[{path:"src/a.py",action:"νέο"}]})');
  assert.equal(document.getElementById('file-list').children[0],rendered);
  console.log('OK safe text preview, scoped request, stable list during polling');
  const prompt=document.getElementById('prompt');prompt.value='abcd';prompt.selectionStart=1;prompt.selectionEnd=3;
  run('insertFilePath()');assert.equal(prompt.value,'aΑρχείο: src/a.py\nd');
  assert(!calls.some(c=>c.url==='/api/send'));
  console.log('OK file path inserted at selection without sending text or file content to a model');
  handler=async(url,body)=>sample({offset:body.offset,next:body.offset===0?20:null,text:'page '+body.offset});
  await run('filePage(true)');assert.equal(document.getElementById('file-text').textContent,'page 20');
  assert.equal(calls.at(-1).body.version,'v1');assert.equal(document.getElementById('file-next').disabled,true);
  await run('filePage(false)');assert.equal(document.getElementById('file-text').textContent,'page 0');
  assert.equal(document.getElementById('file-prev').disabled,true);
  console.log('OK forward/back pages retain server offsets and file version');
  handler=async()=>({cancelled:true});await run('chooseFile()');
  assert.equal(document.getElementById('file-text').textContent,'page 0');
  handler=async()=>({error:'Το αρχείο άλλαξε'});await run('filePage(true)');
  assert.equal(document.getElementById('files-error').textContent,'Το αρχείο άλλαξε');
  assert.equal(document.getElementById('file-text').textContent,'page 0');
  handler=async(url,body)=>{assert.equal(body.version,'');return sample({next:null,text:'fresh'});};
  await run('refreshFile()');assert.equal(document.getElementById('file-text').textContent,'fresh');
  console.log('OK cancel and errors preserve the preview; refresh starts a new version');
  let resolveOld;
  handler=async(url,body)=>body.path==='old.py'?await new Promise(resolve=>resolveOld=resolve):sample({path:'new.py',text:'NEW'});
  const old=run('openProjectFile("old.py")');
  await run('openProjectFile("new.py")');resolveOld(sample({path:'old.py',text:'OLD'}));await old;
  assert.equal(document.getElementById('file-title').textContent,'new.py');assert.equal(document.getElementById('file-text').textContent,'NEW');
  console.log('OK a late file response cannot replace a newer selection');
  handler=async()=>await new Promise(resolve=>resolveOld=resolve);
  const previous=run('openProjectFile("pending.py")');
  await new Promise(resolve=>setImmediate(resolve));
  run('updateFiles({project:"D:/other",session_id:"b",files:[]})');
  resolveOld(sample());await previous;
  assert.equal(document.getElementById('file-preview').hidden,true);assert.equal(document.getElementById('file-text').textContent,'');
  assert.equal(document.getElementById('open-file').disabled,false);
  console.log('OK session/project switch clears preview and ignores stale requests');
  run('toggleFiles(false)');assert.equal(document.getElementById('files-panel').hidden,true);
  run('toggleFiles()');assert.equal(document.getElementById('files-panel').hidden,false);
  run('renderFileList([{path:"<img src=x onerror=bad>",action:"νέο"}])');
  const name=document.getElementById('file-list').children[0].children[0];
  assert.equal(name.textContent,'<img src=x onerror=bad>');assert.equal(name.innerHTML,'');
  console.log('OK panel toggle and untrusted filenames are safe');
  console.log('FILES UI OK');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>clearTimeout(watchdog));
