// Actual application JavaScript, local DOM and fake authenticated API.
const fs=require('node:fs');const path=require('node:path');
let harness=fs.readFileSync(path.join(__dirname,'test_ui.cjs'),'utf8').split('(async()=>{')[0];
harness=harness.replace('window:{getSelection:','window:{location:{hash:"#token=synthetic-token"},getSelection:');
const scenario=String.raw`
(async()=>{
 document.body=new Element('body');
 const seen=[];let pending=true;let globalPending=true;let skillPending=true;
 const injected='<img src=x onerror=bad()>';
 context.fetch=async(url,opt)=>{
   const body=opt.body?JSON.parse(opt.body):{};seen.push({url,body});
   assert.equal(opt.headers['X-Akatikito-Token'],'synthetic-token');
   if(url==='/api/learning/action'){
     if(body.action==='memory_approve'){
       if(body.id==='g'){assert.equal(body.review,'global-review');globalPending=false;}
       else{assert.equal(body.review,'memory-review');pending=false;}
     }
     if(body.action==='skill_reject'){assert.equal(body.review,'skill-review');skillPending=false;}
     return {json:async()=>({ok:true})};
   }
   if(url==='/api/learning')return {json:async()=>({
     project:'D:/synthetic-project',
     profile:{total:31,next:body.offset?null:30,entries:[{id:'e',topic:'Γλώσσα',status:'unconfirmed',text:'Υπόθεση'}],
       proposals:[...(pending?[{id:'m',topic:injected,text:'παλιό',status:'confirmed',review_hash:'memory-review'}]:[]),
         ...(globalPending?[{id:'g',topic:'Γενική σημείωση',text:'αρχικό γενικό',status:'confirmed',target:'global',review_hash:'global-review'}]:[])],
       revision_count:1,history:[{action:'revision',before:[{text:'παλιό'}],after:{text:'νέο'}}]},
     skills:{catalog:[{name:'existing',description:'συνταγή',source:'user'}],
       proposals:skillPending?[{id:'s',name:'recipe',scope:'project',body:injected,signature:'skill-review'}]:[]}
   })};
   throw Error('Unexpected request '+url);
 };
 await run('loadLearning()');
 const panel=document.getElementById('learning-content');
 const descendants=e=>[...e.children,...e.children.flatMap(descendants)];
 let all=[...descendants(panel),...descendants(document.getElementById('skills-content'))];
 const preview=all.find(e=>e.tagName==='PRE');
 assert.equal(preview.textContent,injected);assert.equal(preview.innerHTML,'');
 const input=all.find(e=>e.tagName==='TEXTAREA');input.value='διορθωμένο από χρήστη';
 await all.find(e=>e.textContent==='Έγκριση').onclick();
 const accepted=seen.find(e=>e.body.action==='memory_approve').body;
 assert.equal(accepted.project,'D:/synthetic-project');assert.equal(accepted.id,'m');
 assert.equal(accepted.text,'διορθωμένο από χρήστη');
 all=[...descendants(panel),...descendants(document.getElementById('skills-content'))];
 const globalCard=all.find(e=>e.textContent.includes('ΓΕΝΙΚΗ ΜΝΗΜΗ'));
 assert(globalCard && globalCard.textContent.includes('ΟΛΑ τα projects'));
 globalCard.children.find(e=>e.tagName==='TEXTAREA').value='διορθωμένο γενικό';
 await globalCard.children.find(e=>e.textContent==='Έγκριση').onclick();
 const globalAccepted=seen.find(e=>e.body.id==='g').body;
 assert.equal(globalAccepted.action,'memory_approve');assert.equal(globalAccepted.text,'διορθωμένο γενικό');
 assert.equal(globalAccepted.project,'D:/synthetic-project');
 all=[...descendants(panel),...descendants(document.getElementById('skills-content'))];await all.find(e=>e.textContent==='Απόρριψη').onclick();
 assert(seen.find(e=>e.body.action==='skill_reject'));
 all=[...descendants(panel),...descendants(document.getElementById('skills-content'))];await all.find(e=>e.textContent==='Επόμενες').onclick();
 assert(seen.some(e=>e.url==='/api/learning'&&e.body.offset===30));
 console.log('OK owner memory edits, exact preview keys, inert skill preview, rejection and pagination');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\n'+scenario);
