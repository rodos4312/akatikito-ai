const fs=require('node:fs'),path=require('node:path');
const harness=fs.readFileSync(path.join(__dirname,'test_ui.cjs'),'utf8').split('(async()=>{')[0];
const scenario=String.raw`
(async()=>{
 const seen=[];const plugin={id:'telegram',name:'Telegram',version:'1',source:'catalog:telegram',description:'Telegram channel'};
 const draft={id:'draft',review:'review-hash',source:'catalog:telegram',manifest:{name:'Telegram',permissions:['network']},parts:{'adapter.py':'<script>untrusted()</script>'}};
 let installed=false;
 context.fetch=async(url,opt)=>{
   const body=opt?.body?JSON.parse(opt.body):{};seen.push({url,body});
   if(url==='/api/plugins')return {json:async()=>({available:[plugin],installed:installed?[plugin]:[],config:{telegram:{has_token:true,allowed_users:['10'],allowed_chats:[],project:'D:/project'}},gateway:{running:false}})};
   if(url==='/api/plugins/action'){
     if(body.action==='preview')return {json:async()=>draft};
     if(body.action==='install')installed=true;
     return {json:async()=>({ok:true})};
   }
   if(url==='/api/learning')return {json:async()=>({project:'D:/project',profile:{entries:[{id:'fact',topic:'Preference',text:'memory-only'}]},skills:{catalog:[{name:'skill-one',description:'skill-only',source:'builtin'}]}})};
   if(url==='/api/channel/session')return {json:async()=>({title:'Telegram history',source:{platform:'telegram'},transcript:[{role:'user',text:'remote-only'}],readonly:true})};
   throw Error('Unexpected URL '+url);
 };
 const descendants=e=>[...e.children,...e.children.flatMap(descendants)];
 for(const name of ['sess','providers','plugins','skills','memory','set']){
   run('showTab('+JSON.stringify(name)+')');await new Promise(r=>setImmediate(r));
   assert.equal(document.getElementById('management').style.display,name==='sess'?'none':'block');
   assert.equal(document.getElementById('chat-workspace').style.display,name==='sess'?'flex':'none');
   for(const other of ['sess','providers','plugins','skills','memory','set'])assert.equal(document.getElementById('view-'+other).style.display,name===other?'flex':'none');
 }
 const memory=descendants(document.getElementById('learning-content')).map(x=>x.textContent).join(' ');
 const skills=descendants(document.getElementById('skills-content')).map(x=>x.textContent).join(' ');
 assert(memory.includes('memory-only')&&!memory.includes('skill-only'));
 assert(skills.includes('skill-only')&&!skills.includes('memory-only'));
 await run('previewPlugin("catalog:telegram")');
 assert(!seen.some(x=>x.body.action==='install'||x.body.action==='start'));
 const review=descendants(document.getElementById('plugin-review'));
 const code=review.find(x=>x.tagName==='PRE');assert.equal(code.textContent,draft.parts['adapter.py']);assert.equal(code.innerHTML,'');
 await review.find(x=>x.textContent==='Εγκρίνω και εγκαθιστώ αυτή την έκδοση').onclick();
 assert(seen.some(x=>x.body.action==='install'&&x.body.review==='review-hash'&&x.body.id==='draft'));
 run('configureChannel("telegram")');
 const fields=descendants(document.getElementById('plugin-config'));
 assert.equal(fields.find(x=>x.id==='channel-token').type,'password');
 const token=fields.find(x=>x.id==='channel-token');token.value='synthetic-token';
 fields.find(x=>x.id==='channel-enabled').checked=true;
 await fields.find(x=>x.textContent==='Αποθήκευση σύνδεσης').onclick();
 assert.equal(token.value,'');assert(seen.some(x=>x.body.action==='configure'&&x.body.config.allowed_users[0]==='10'));
 assert(!seen.some(x=>x.body.action==='start'));
 run('chat.replaceChildren();addMsg("user","desktop-only")');const before=document.getElementById('chat').children.length;
 await run('viewChannelSession({id:"remote",project:"D:/project"})');
 assert.equal(document.getElementById('chat').children.length,before);
 assert(descendants(document.getElementById('channel-session-overlay')).some(x=>x.textContent==='remote-only'));
 assert(!seen.some(x=>x.url==='/api/send'||x.url==='/api/connect'||x.url==='/api/models'));
 console.log('OK six independent menus, separated skills/memory, reviewed install, masked channel config and isolated remote history');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\n'+scenario);
