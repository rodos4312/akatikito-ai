// First-run flow. Credentials stay in input memory; no automatic catalog/chat calls.
let setupOpen=false,setupStep=0,setupSubmitting=false,setupRequest=0,setupInert=[];
var setupSimplePath='choose';
const setupEl=id=>document.getElementById('setup-'+id);
var SETUP_CLOUD_LINKS={
  'OpenAI':'https://platform.openai.com/api-keys',
  'Anthropic (Claude)':'https://console.anthropic.com/settings/keys',
  'Google Gemini':'https://aistudio.google.com/apikey',
  'DeepSeek':'https://platform.deepseek.com/api_keys',
};
const SETUP_EXAMPLES=[
  {key:'setup_ex_1',fallback:'Τι μπορείς να κάνεις;'},
  {key:'setup_ex_2',fallback:'Θυμήσου ότι προτιμώ σύντομες απαντήσεις στα ελληνικά'},
  {key:'setup_ex_3',fallback:'Βοήθησέ με με ένα email'},
];

function _tSetup(key,fallback){return typeof i18nText==='function'?i18nText(key,fallback):fallback;}

function setupRenderSimple(){
  const box=setupEl('simple');
  if(!box)return;
  const linksHtml=Object.entries(SETUP_CLOUD_LINKS).map(([name,url])=>`<a href="${url}" target="_blank" rel="noopener">${name}</a>`).join(' · ');
  box.innerHTML=
    `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">`+
      `<strong>${_tSetup('setup_how_title','Πώς θέλεις να δουλεύει;')}</strong>`+
      `<select aria-label="Language" onchange="if(typeof changeUiLanguage==='function')changeUiLanguage(this.value)" style="width:auto">`+
        `<option value="el">Ελληνικά</option><option value="en">English</option><option value="ru">Русский</option>`+
        `<option value="de">Deutsch</option><option value="es">Español</option><option value="fr">Français</option>`+
      `</select>`+
    `</div>`+
    `<div style="display:flex;flex-direction:column;gap:6px">`+
      `<button class="btn primary" onclick="setupChoosePath('cloud')">${_tSetup('setup_opt_cloud','☁️ Με λογαριασμό AI (OpenAI, Anthropic, Google, DeepSeek)')}</button>`+
      `<button class="btn" onclick="setupChoosePath('local')">${_tSetup('setup_opt_local','💻 Τοπικά στον υπολογιστή μου (δωρεάν, πιο αργό)')}</button>`+
      `<button class="btn" onclick="setupChoosePath('advanced')">${_tSetup('setup_opt_advanced','⚙️ Για προχωρημένους')}</button>`+
    `</div>`+
    `<div id="setup-simple-detail" class="hint" style="margin-top:6px">Κλειδιά: ${linksHtml}</div>`;
}

async function setupDetectLocal(){
  const r=await api('/api/onboarding/detect-local',{});
  const detail=document.getElementById('setup-simple-detail');
  if(r&&Array.isArray(r.found)&&r.found.length>0){
    const first=r.found[0];
    if(setupEl('provider'))setupEl('provider').value='Custom';
    if(setupEl('url'))setupEl('url').value=first.base_url||'';
    if(setupEl('model')&&first.models?.[0])setupEl('model').value=first.models[0];
    if(detail)detail.textContent=`Βρέθηκε ${first.name} (${(first.models||[]).join(', ')||'έτοιμο'}).`;
  }else if(detail){
    const links=r?.install_links||{ollama:'https://ollama.com',lm_studio:'https://lmstudio.ai'};
    detail.innerHTML=`Δεν βρέθηκε Ollama ή LM Studio στον υπολογιστή σου. Εγκατέστησε προαιρετικά: <a href="${links.ollama}" target="_blank" rel="noopener">Ollama</a> ή <a href="${links.lm_studio}" target="_blank" rel="noopener">LM Studio</a> (ποτέ αυτόματο κατέβασμα).`;
  }
  return r;
}

async function setupChoosePath(mode){
  setupSimplePath=mode||'advanced';
  const detail=document.getElementById('setup-simple-detail');
  if(setupSimplePath==='cloud'){
    if(setupEl('provider')&&!SETUP_CLOUD_LINKS[setupEl('provider').value])setupEl('provider').value='OpenAI';
    setupProviderChanged();
    setupStep=1;setupRender();
    if(detail)detail.textContent='Επίλεξε πάροχο, επικόλλησε το κλειδί API και πάτησε Επόμενο.';
    return;
  }
  if(setupSimplePath==='local'){
    await setupDetectLocal();
    setupStep=2;setupRender();
    return;
  }
  setupStep=0;setupRender();
}

function setupShowExamples(){
  const box=setupEl('examples');
  if(!box)return;
  box.style.display='flex';
  box.style.gap='6px';
  box.style.flexWrap='wrap';
  box.innerHTML=SETUP_EXAMPLES.map((ex,idx)=>`<button class="btn" style="width:auto" onclick="setupRunExample(${idx})">${_tSetup(ex.key,ex.fallback)}</button>`).join('');
}

function setupRunExample(idx){
  const ex=SETUP_EXAMPLES[idx];
  if(!ex)return;
  const text=_tSetup(ex.key,ex.fallback);
  if(typeof useQuickPrompt==='function'){useQuickPrompt(text);return;}
  const p=document.getElementById('prompt');
  if(p){p.value=text;p.focus?.();}
}

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
  setupEl('dialog').style.display='flex';setupRenderSimple();setupRender();setupEl('project').focus();
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
    const body={...credentials,model,project,max_steps:document.getElementById('max_steps').value||50,verify:document.getElementById('verify').value||''};
    let r=await api('/api/connect',body);
    if(r.requires_key_confirmation&&confirm('Το αποθηκευμένο κλειδί ανήκει αλλού. Να χρησιμοποιηθεί στη διεύθυνση '+r.destination+';'))r=await api('/api/connect',{...body,confirm_key_destination:true});
    if(r.error||!r.connected){setupEl('error').textContent=r.error||'Η σύνδεση δεν ολοκληρώθηκε.';return;}
    setupSubmitting=false;setupShowExamples();closeSetup();acceptConnection(r,credentials.provider);await loadProviders();
  }catch(e){setupEl('error').textContent='Δεν ολοκληρώθηκε η σύνδεση· έλεγξε τις ρυθμίσεις και δοκίμασε ξανά.';}
  finally{setupSubmitting=false;setupRender();}
}

document.addEventListener('keydown',e=>{if(e.key==='Escape'&&setupOpen&&!setupSubmitting)closeSetup();});
