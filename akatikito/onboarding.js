// First-run flow. Credentials stay in input memory; no automatic catalog/chat calls.
let setupOpen=false,setupStep=0,setupSubmitting=false,setupRequest=0,setupInert=[];
const setupEl=id=>document.getElementById('setup-'+id);

function setupRender(){
  for(let i=0;i<4;i++)setupEl('page-'+i).style.display=i===setupStep?'flex':'none';
  setupEl('progress').textContent='Βήμα '+(setupStep+1)+' από 4';
  setupEl('back').disabled=setupStep===0||setupSubmitting;
  setupEl('next').style.display=setupStep===3?'none':'';
  setupEl('finish').style.display=setupStep===3?'':'none';
  for(const id of ['next','finish','later'])setupEl(id).disabled=setupSubmitting;
  for(const id of ['project','provider','key','model','test'])setupEl(id).disabled=setupSubmitting;
  setupEl('url').disabled=setupSubmitting||setupEl('provider').value!=='Custom';
  if(setupStep===3)setupEl('summary').textContent='Φάκελος: '+setupEl('project').value.trim()+'\nΠάροχος: '+setupEl('provider').value+'\nΜοντέλο: '+setupEl('model').value.trim();
}

async function openSetup(data=null){
  if(setupOpen)return;
  if(busy){showNotice('err','Περίμενε να ολοκληρωθεί η εργασία.');return;}
  const r=data?.providers?data:await api('/api/providers');
  if(r.error){showNotice('err',r.error);return;}
  setupOpen=true;setupStep=0;setupRequest++;setupEl('error').textContent='';
  setupEl('provider').replaceChildren(...(r.providers||[]).map(p=>new Option(p,p)));
  setupEl('provider').value=r.current||r.providers?.[0]||'';
  setupEl('project').value=r.project||'';setupEl('key').value='';
  setupProviderChanged();setupEl('url').value=r.current==='Custom'?(r.base_url||''):'';
  setupEl('model').value=r.model||r.models?.[r.current]||setupEl('model').value;
  setupEl('dialog').style.display='flex';setupRender();setupEl('project').focus();
  const panels=document.body?.children?Array.from(document.body.children):['sidebar','main','files-panel'].map(id=>document.getElementById(id));
  setupInert=panels.filter(el=>el!==setupEl('dialog')&&el.tagName!=='SCRIPT').map(el=>[el,el.inert]);
  for(const [el] of setupInert)el.inert=true;
}

function closeSetup(){
  if(setupSubmitting)return;
  setupOpen=false;setupRequest++;setupEl('key').value='';setupEl('dialog').style.display='none';
  for(const [el,previous] of setupInert)el.inert=previous;setupInert=[];
  document.getElementById('tab-set').focus();
}

function setupProviderChanged(){
  setupRequest++;setupEl('key').value='';const p=setupEl('provider').value;
  setupEl('url').disabled=p!=='Custom';setupEl('url').value='';
  setupEl('key-hint').textContent=savedKeys[p]?'Υπάρχει αποθηκευμένο κλειδί· μπορείς να αφήσεις το πεδίο κενό.':'Για τοπικό πάροχο μπορεί να μη χρειάζεται κλειδί.';
  setupEl('models').replaceChildren(...(suggested[p]||[]).map(m=>new Option(m,m)));
  setupEl('model').value=providerModels[p]||(suggested[p]||[])[0]||'';
}

async function setupPickFolder(){
  const r=await api('/api/pick-folder',{});
  if(r.path&&setupOpen)setupEl('project').value=r.path;
  else if(r.error)setupEl('error').textContent=r.error;
}

function setupNext(){
  if(setupSubmitting)return;setupEl('error').textContent='';
  if(setupStep===0&&!setupEl('project').value.trim()){setupEl('error').textContent='Επίλεξε πρώτα φάκελο project.';return;}
  if(setupStep===1&&(!setupEl('provider').value||(setupEl('provider').value==='Custom'&&!/^https?:\/\//i.test(setupEl('url').value.trim())))){setupEl('error').textContent='Διάλεξε πάροχο και έγκυρη διεύθυνση για Custom.';return;}
  if(setupStep===2&&!setupEl('model').value.trim()){setupEl('error').textContent='Γράψε ή διάλεξε ένα μοντέλο.';return;}
  setupStep=Math.min(3,setupStep+1);setupRender();
}
function setupBack(){if(!setupSubmitting){setupStep=Math.max(0,setupStep-1);setupEl('error').textContent='';setupRender();}}
function setupCredentials(){return {provider:setupEl('provider').value,base_url:setupEl('provider').value==='Custom'?setupEl('url').value.trim():'',api_key:setupEl('key').value};}

async function setupTestModels(){
  const generation=++setupRequest;const body=setupCredentials();const snapshot=JSON.stringify(body);
  setupEl('test').disabled=true;setupEl('error').textContent='';
  try{
    const r=await api('/api/models',body);
    if(!setupOpen||generation!==setupRequest||snapshot!==JSON.stringify(setupCredentials()))return;
    if(r.error){setupEl('error').textContent=r.error;return;}
    setupEl('models').replaceChildren(...(r.models||[]).map(m=>new Option(m,m)));
    if(!setupEl('model').value.trim())setupEl('model').value=r.models?.[0]||'';
    setupEl('error').textContent='Η σύνδεση απάντησε με '+(r.models||[]).length+' μοντέλα.';
  }catch(e){if(setupOpen)setupEl('error').textContent='Δεν ολοκληρώθηκε η δοκιμή σύνδεσης.';}
  finally{setupEl('test').disabled=false;}
}

async function finishSetup(){
  if(setupSubmitting||setupStep!==3)return;
  const credentials=setupCredentials();const model=setupEl('model').value.trim(),project=setupEl('project').value.trim();
  if(!model||!project){setupEl('error').textContent='Συμπλήρωσε φάκελο και μοντέλο.';return;}
  setupSubmitting=true;setupRender();setupEl('error').textContent='';
  try{
    const body={...credentials,model,project,max_steps:document.getElementById('max_steps').value||25,verify:document.getElementById('verify').value||''};
    let r=await api('/api/connect',body);
    if(r.requires_key_confirmation&&confirm('Το αποθηκευμένο κλειδί ανήκει αλλού. Να χρησιμοποιηθεί στη διεύθυνση '+r.destination+';'))r=await api('/api/connect',{...body,confirm_key_destination:true});
    if(r.error||!r.connected){setupEl('error').textContent=r.error||'Η σύνδεση δεν ολοκληρώθηκε.';return;}
    setupSubmitting=false;closeSetup();acceptConnection(r,credentials.provider);await loadProviders();
  }catch(e){setupEl('error').textContent='Δεν ολοκληρώθηκε η σύνδεση· έλεγξε τις ρυθμίσεις και δοκίμασε ξανά.';}
  finally{setupSubmitting=false;setupRender();}
}

document.addEventListener('keydown',e=>{if(e.key==='Escape'&&setupOpen&&!setupSubmitting)closeSetup();});
