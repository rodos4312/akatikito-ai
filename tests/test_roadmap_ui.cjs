// Reuse the existing local DOM harness, without a browser or model calls.
const fs=require('node:fs');const path=require('node:path');
let harness=fs.readFileSync(path.join(__dirname,'test_ui.cjs'),'utf8').split('(async()=>{')[0];
harness=harness.replace('window:{getSelection:','window:{location:{hash:"#token=synthetic-token"},getSelection:');
const scenarios=String.raw`
(async()=>{
 document.body=new Element('body');Element.prototype.showModal=function(){this.open=true;};
 const seen=[];const previous=context.fetch;
 context.fetch=async(url,opt)=>{
   assert.equal(opt.headers['X-Akatikito-Token'],'synthetic-token');
   const body=opt.body?JSON.parse(opt.body):{};seen.push({url,body});
   if(url==='/api/checkpoint/diff')return {json:async()=>({changes:[{path:'x.py',diff:'-old\n+new'}]})};
   if(url==='/api/backups')return {json:async()=>({backups:[]})};
   if(url==='/api/approval')return {json:async()=>({ok:true})};
   return previous(url,opt);
 };
 await run('init()');
 assert(document.getElementById('mode').children.some(x=>x.value==='assistant'));
 run("renderApprovals([{id:'danger',project:'fixture',risk:'dangerous',kind:'command',details:{command:'rm fixture'}}])");
 const buttons=document.getElementById('permission-panel').children[0].children.filter(x=>x.tagName==='BUTTON');
 assert.equal(buttons.length,2);await buttons[1].onclick();assert.equal(seen.at(-1).body.allow,false);
 run("renderApprovals([{id:'norm',project:'fixture',risk:'normal',kind:'command',details:{command:'python -c 1'}}])");
 const normBtns=document.getElementById('permission-panel').children[0].children.filter(x=>x.tagName==='BUTTON');
 assert.equal(normBtns.length,4);await normBtns[3].onclick();assert.equal(seen.at(-1).body.autonomous,true);
 // Smart auto-scroll: scrolled-up chat must NOT be yanked back to bottom by setStatus/streamEvent
 const chatEl=document.getElementById('chat');
 chatEl.scrollHeight=1000;chatEl.clientHeight=400;chatEl.scrollTop=100;
 run("addStep('step',{step:3,of:50});setStatus('working');streamEvent('stream_start',{stream_id:'s1',model:'m'});streamEvent('stream_reasoning',{stream_id:'s1',text:'thinking...'});");
 assert.equal(chatEl.scrollTop,100);
 run("updateBadges({connected:true,busy:true,model:'m',provider:'p',mode:'code',tokens:{prompt_tokens:12000,completion_tokens:500},preferences:{advanced_view:false},todos:[],subtasks:[],reminders:[]})");
 assert(document.getElementById('busy-badge').textContent.includes('βήμα 3/50'));
 assert(document.getElementById('busy-badge').textContent.includes('12.5k tok'));
 run("renderTranscript([{role:'assistant',text:'done',checkpoint:'123'}])");
 const checkpoint=document.getElementById('chat').querySelectorAll('.msg').at(-1).children.find(x=>x.textContent==='Αλλαγές / Αναίρεση');
 assert(checkpoint);await checkpoint.onclick();assert(document.body.children.at(-1).children[0].textContent.includes('+new'));
 run("attachments=[{id:'draft',name:'draft.txt'}];attachmentContext='old|session';updateBadges({project:'new',session_id:'new',preferences:{advanced_view:false},todos:[{text:'Run tests',status:'in_progress'}],subtasks:[],reminders:[]})");
 assert.equal(run('attachments.length'),0);assert(document.getElementById('task-panel').children[0].textContent.includes('Run tests'));
 run("connected=true;currentProject='p';currentSession='s';attachments=[{id:'x',name:'attached.txt'}];attachmentContext='p|s'");
 status={...status,project:'p',session_id:'s'};
 document.getElementById('prompt').value='Analyse attachment';sendResult={error:'synthetic rejection'};
 await run('send()');assert.equal(run('attachments.length'),1);assert.equal(document.getElementById('prompt').value,'Analyse attachment');
 assert(seen.find(x=>x.url==='/api/send').body.attachments[0].id==='x');
 console.log('OK authenticated UI, assistant selector, permission refusal, checkpoint diff, todos, attachment context and rejected-send retention');
})().catch(err=>{console.error(err);process.exitCode=1;});
`;
new Function('require','__dirname',harness+scenarios)(require,__dirname);
