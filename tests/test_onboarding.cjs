const fs=require('node:fs'),path=require('node:path');
const harness=fs.readFileSync(path.join(__dirname,'test_ui.cjs'),'utf8').split('(async()=>{')[0];
const scenario=String.raw`
(async()=>{
 const seen=[];let failConnect=true,failModels=true,pendingModels=null;
 Object.assign(providers,{providers:['Custom','DeepSeek'],current:'Custom',base_url:'',model:'',models:{},project:'',suggested:{Custom:[],DeepSeek:['suggested/model']},keys:{},onboarding:{complete:false}});
 status={connected:false,busy:false,tokens:{},project:'',transcript:[]};
 context.fetch=async(url,opt)=>{
  const body=opt.body?JSON.parse(opt.body):{};seen.push({url,body});
  if(url==='/api/providers')return {json:async()=>providers};
  if(url==='/api/status'||url==='/api/poll')return {json:async()=>status};
  if(url==='/api/sessions')return {json:async()=>({sessions:[]})};
  if(url==='/api/backups')return {json:async()=>({backups:[]})};
  if(url==='/api/pick-folder')return {json:async()=>({path:'D:/synthetic-project'})};
  if(url==='/api/models'){
    if(pendingModels)return {json:()=>new Promise(resolve=>{pendingModels=resolve;})};
    return {json:async()=>failModels?{error:'synthetic catalog failure'}:{models:['catalog/model']}};
  }
  if(url==='/api/connect'){
    if(failConnect)return {json:async()=>({error:'synthetic connection failure'})};
    Object.assign(providers,{project:body.project,model:body.model,current:body.provider,base_url:body.base_url,onboarding:{complete:true}});
    status={connected:true,busy:false,provider:body.provider,model:body.model,project:body.project,mode:'code',tokens:{},onboarding:{complete:true}};
    return {json:async()=>status};
  }
  throw Error('Unexpected route '+url);
 };
 await run('init()');
 assert.equal(run('setupOpen'),true);assert.equal(run('setupStep'),0);
 assert.equal(document.getElementById('files-panel').inert,true);
 assert(!seen.some(r=>r.url==='/api/models'||r.url==='/api/connect'||r.url==='/api/send'));
 run('setupNext()');assert.equal(run('setupStep'),0);
 await run('setupPickFolder()');run('setupNext()');assert.equal(run('setupStep'),1);
 run('setupNext()');assert.equal(run('setupStep'),1);
 run('setupEl("url").value="https://synthetic.invalid/v1";setupEl("key").value="synthetic-secret";setupNext()');
 assert.equal(run('setupStep'),2);
 run('setupBack()');assert.equal(run('setupEl("key").value'),'synthetic-secret');run('setupNext()');
 await run('setupTestModels()');assert.match(run('setupEl("error").textContent'),/catalog failure/);
 run('setupEl("model").value="manual/model";setupNext()');
 assert.equal(run('setupStep'),3);assert(!run('setupEl("summary").textContent').includes('synthetic-secret'));
 await run('finishSetup()');assert.equal(run('setupOpen'),true);assert.match(run('setupEl("error").textContent'),/connection failure/);
 assert.equal(run('setupEl("key").value'),'synthetic-secret');
 failConnect=false;await run('finishSetup()');
 assert.equal(run('setupOpen'),false);assert.equal(run('setupEl("key").value'),'');
 assert.notEqual(document.getElementById('files-panel').inert,true);
 assert.equal(providers.onboarding.complete,true);
 assert.equal(seen.filter(r=>r.url==='/api/connect').length,2);
 assert(!seen.some(r=>r.url==='/api/send'));
 await run('openSetup()');assert.equal(run('setupStep'),0);
 run('setupEl("key").value="clear-on-close";closeSetup()');assert.equal(run('setupEl("key").value'),'');
 assert.equal(seen.filter(r=>r.url==='/api/connect').length,2);
 await run('openSetup()');pendingModels=true;
 const request=run('setupTestModels()');await new Promise(resolve=>setImmediate(resolve));
 run('setupEl("provider").value="DeepSeek";setupProviderChanged()');
 const resolve=pendingModels;pendingModels=null;resolve({models:['stale/model']});await request;
 assert(!run('setupEl("models").children.map(x=>x.value)').includes('stale/model'));
 run('closeSetup()');await run('init()');assert.equal(run('setupOpen'),false);
 console.log('OK first run, validation, back, manual model, explicit catalog, errors, success, reopen, no chat, stale response');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\n'+scenario);
