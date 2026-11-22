// Drive real UI handlers with a synthetic MediaRecorder, without a microphone or network.
const fs=require('node:fs'),path=require('node:path');
const harness=fs.readFileSync(path.join(__dirname,'test_ui.cjs'),'utf8').split('(async()=>{')[0];
const scenarios=String.raw`
(async()=>{
 const calls=[],tracks=[],recorders=[],timers=[];let next=0,holdUpload=null;
 const flush=async()=>{for(let i=0;i<6;i++)await new Promise(setImmediate);};
 context.Blob=Blob;
 context.setTimeout=(fn,delay)=>{timers.push({fn,delay});return timers.length;};
 context.FileReader=class{readAsDataURL(blob){blob.arrayBuffer().then(raw=>{this.result='data:'+blob.type+';base64,'+Buffer.from(raw).toString('base64');this.onload();});}};
 const stream=()=>{const row={stopped:0,stop(){this.stopped++;}};tracks.push(row);return {getTracks:()=>[row]};};
 context.navigator={mediaDevices:{getUserMedia:async()=>stream()}};
 context.MediaRecorder=class{
   static isTypeSupported(type){return type.startsWith('audio/webm');}
   constructor(stream,options){this.state='inactive';this.mimeType=options.mimeType;recorders.push(this);}
   start(){this.state='recording';}
   stop(){this.state='inactive';this.ondataavailable({data:new Blob(['synthetic audio'],{type:this.mimeType})});Promise.resolve().then(()=>this.onstop());}
 };
 context.fetch=async(url,options)=>{
   const body=options?.body?JSON.parse(options.body):{};calls.push({url,body});
   if(url==='/api/voice/transcribe'){
     if(holdUpload)await holdUpload;
     return {json:async()=>({id:'v'+(++next),kind:'transcribe',status:'transcribing',context:[body.project,body.session_id]})};
   }
   if(url==='/api/voice/cancel')return {json:async()=>({ok:true})};
   throw Error('Unexpected request '+url);
 };
 run("connected=true;busy=false;currentProject='fixture';currentSession='one'");
 const render=(job=null)=>{context.fixtureVoice={voice:{model:'base',language:'el',models:{base:true},job},busy:false};return run('renderVoice(fixtureVoice)');};
 const draft=text=>{const el=document.getElementById('prompt');el.value=text;el.selectionStart=el.selectionEnd=text.length;};
 render({id:'setup',kind:'prepare',status:'complete'});assert(document.getElementById('voice-message').textContent.includes('έτοιμο'));render();draft('Αρχικό');
 await run('toggleVoice()');assert.equal(recorders.length,1);assert.equal(recorders[0].state,'recording');
 await run('send()');assert.equal(document.getElementById('prompt').value,'Αρχικό');assert.equal(calls.length,0);
 await run('toggleVoice()');await flush();
 assert.equal(calls.filter(x=>x.url==='/api/voice/transcribe').length,1);assert(tracks[0].stopped>0);
 render({id:'v1',kind:'transcribe',status:'complete',context:['fixture','one'],text:'Ελληνική υπαγόρευση'});
 assert.equal(document.getElementById('prompt').value,'Αρχικό Ελληνική υπαγόρευση');
 render({id:'v1',kind:'transcribe',status:'complete',context:['fixture','one'],text:'Ελληνική υπαγόρευση'});
 assert.equal(document.getElementById('prompt').value,'Αρχικό Ελληνική υπαγόρευση');assert(!calls.some(x=>x.url==='/api/send'));
 draft('Πρώτο');await run('toggleVoice()');await run('toggleVoice()');await flush();draft('Χειροκίνητη αλλαγή');
 render({id:'v2',kind:'transcribe',status:'complete',context:['fixture','one'],text:'δεύτερο'});
 assert.equal(document.getElementById('prompt').value,'Χειροκίνητη αλλαγή');
 await run('insertPendingVoice()');assert.equal(document.getElementById('prompt').value,'Χειροκίνητη αλλαγή δεύτερο');
 render();draft('Να παραμείνει');await run('toggleVoice()');await run('cancelVoice()');await flush();
 assert.equal(document.getElementById('prompt').value,'Να παραμείνει');assert.equal(calls.filter(x=>x.url==='/api/voice/transcribe').length,2);
 // A cancelled permission request must release tracks if permission resolves later.
 let grant;context.navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>{grant=resolve;});
 const pending=run('toggleVoice()');await run('cancelVoice()');grant(stream());await pending;await flush();assert(tracks.at(-1).stopped>0);
 // Permission denial preserves the draft and sends nothing.
 context.navigator.mediaDevices.getUserMedia=async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});};
 await run('toggleVoice()');assert.equal(run('voiceCapture'),null);assert.equal(document.getElementById('prompt').value,'Να παραμείνει');
 context.navigator.mediaDevices.getUserMedia=async()=>stream();render();
 await run('toggleVoice()');run("currentSession='two'");render();await flush();assert.equal(calls.filter(x=>x.url==='/api/voice/transcribe').length,2);
 // Cancel while the HTTP upload is in flight: its later job must also be cancelled.
 run("currentSession='one'");render();let release;holdUpload=new Promise(resolve=>{release=resolve;});
 await run('toggleVoice()');await run('toggleVoice()');await flush();assert.equal(run('voiceUploading'),true);
 await run('cancelVoice()');release();await flush();holdUpload=null;
 assert(calls.some(x=>x.url==='/api/voice/cancel'&&x.body.id==='v3'));
 render({id:'v3',kind:'transcribe',status:'complete',context:['fixture','one'],text:'must not insert'});
 assert.equal(document.getElementById('prompt').value,'Να παραμείνει');
 // Time limit stops capture; the result remains a draft, never a send operation.
 render();await run('toggleVoice()');assert(timers.some(x=>x.delay===60000));timers.filter(x=>x.delay===60000).at(-1).fn();await flush();
 assert.equal(recorders.at(-1).state,'inactive');assert(!calls.some(x=>x.url==='/api/send'));
 console.log('OK synthetic microphone, Greek draft, repeat polling, edited draft, permission denial, late permission, context switch, upload cancellation and 60s limit');
})().catch(error=>{console.error(error);process.exitCode=1;});
`;
new Function('require','__dirname',harness+scenarios)(require,__dirname);
