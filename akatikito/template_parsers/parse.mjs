// Parse source as data only: no compile, preprocess, project config, eval or imports from source.
import {readFileSync} from 'node:fs';
const request=JSON.parse(readFileSync(0,'utf8'));
const {source,framework}=request;
const out={rows:[],regions:[],notices:[],cacheable:true};
const max=4000;let visited=0;
const offsets=new Uint32Array(source.length+1);
for(let i=0,b=0;i<source.length;){const point=source.codePointAt(i),n=point>65535?2:1;offsets[i]=b;if(n===2)offsets[i+1]=b;b+=Buffer.byteLength(String.fromCodePoint(point));i+=n;offsets[i]=b;}
function notice(s){if(out.notices.length<16)out.notices.push(String(s).slice(0,200));}
function add(a,b,name,kind='template',header=name){if(out.rows.length>=max){out.cacheable=false;notice('Όριο συμβόλων template.');return;}if(Number.isInteger(a)&&Number.isInteger(b)&&a>=0&&a<b&&b<=source.length)out.rows.push({start:offsets[a],end:offsets[b],name:String(name).slice(0,180),header:String(header).slice(0,256),kind});}
function region(a,b,name,lang,src){if(out.regions.length>=128){out.cacheable=false;notice('Όριο embedded regions.');return;}if(Number.isInteger(a)&&Number.isInteger(b)&&a<=b)out.regions.push({start:offsets[a],end:offsets[b],name,language:lang,src:typeof src==='string'?src:null});}
function walk(roots,visitor,children){const stack=[...roots].reverse(),seen=new Set();while(stack.length){if(++visited>100000){notice('Όριο κόμβων template.');out.cacheable=false;break;}const n=stack.pop();if(!n||typeof n!=='object'||seen.has(n))continue;seen.add(n);if(visitor(n)===false)continue;const kids=children(n);for(let i=kids.length-1;i>=0;i--)stack.push(kids[i]);}}
function lang(value,style=false){if(!value)return style?'.css':'.js';return (style?{css:'.css'}:{js:'.js',javascript:'.js',ts:'.ts',typescript:'.ts',jsx:'.jsx',tsx:'.tsx'})[value]??null;}
try{
 if(framework==='css'){
  const css=await import('css-tree');const errors=[];
  const ast=css.parse(source,{positions:true,onParseError:e=>errors.push(e.message)});
  css.walk(ast,n=>{if(++visited>100000){out.cacheable=false;throw new RangeError('Όριο κόμβων CSS.');}if(n.type==='Rule'||n.type==='Atrule'){
   if(n.block&&source[n.block.loc.end.offset-1]!=='}'){errors.push('Ατελές CSS block.');return;}
   const a=n.loc.start.offset,b=n.loc.end.offset,e=n.block?n.block.loc.start.offset:b;
   const header=source.slice(a,e).replace(/\s+/g,' ').trim();add(a,b,header,'css',header);
  }});
  for(const message of errors)notice(message);
 }else if(framework==='vue'){
  const {parse}=await import('@vue/compiler-sfc');
  const parsed=parse(source,{filename:'source.vue',sourceMap:false,ignoreEmpty:false});
  if(parsed.errors.length){for(const e of parsed.errors)notice(e.message??e);}
  else{
   const d=parsed.descriptor;
   for(const [block,label,style] of [[d.script,'script',false],[d.scriptSetup,'script setup',false],...d.styles.map((b,i)=>[b,'style['+(i+1)+']',true])]){
    if(!block)continue;const language=lang(block.lang,style);if(!language){notice(label+': μη υποστηριζόμενο lang='+block.lang);continue;}
    if(!style&&block.attrs.type&&!['module','text/javascript','application/javascript','text/typescript'].includes(block.attrs.type)){notice(label+': μη υποστηριζόμενο type');continue;}
    region(block.loc.start.offset,block.loc.end.offset,label,language,block.src);
   }
   if(d.template){const t=d.template;if(t.src)notice('Εξωτερικό template: δεν φορτώνεται.');else if(t.lang&&t.lang!=='html')notice('Μη υποστηριζόμενο template lang='+t.lang);else{
    add(t.loc.start.offset,t.loc.end.offset,'template');
    walk(t.ast?.children??[],n=>{if(n.type===1)add(n.loc.start.offset,n.loc.end.offset,'<'+n.tag+'>');else if(n.type===7)add(n.loc.start.offset,n.loc.end.offset,n.rawName??('v-'+n.name),'binding');else if(n.type===5)add(n.loc.start.offset,n.loc.end.offset,'{{ interpolation }}','binding');},n=>[...(n.children??[]),...(n.props??[])]);
   }}
   for(const b of d.customBlocks)notice('Custom block '+b.type+': περιεχόμενο ως κείμενο.');
  }
 }else if(framework==='svelte'){
  const {parse}=await import('svelte/compiler');const ast=parse(source,{modern:true});
  for(const [block,label] of [[ast.instance,'script instance'],[ast.module,'script module']]){if(!block)continue;const attr=block.attributes?.find(a=>a.name==='lang');const language=lang(attr?.value?.[0]?.data);if(language)region(block.content.start,block.content.end,label,language);else notice(label+': μη υποστηριζόμενο lang');}
  if(ast.css)region(ast.css.content.start,ast.css.content.end,'style','.css');
  walk([ast.fragment],n=>{if(n.type==='Comment'||n.type==='Text')return false;if(n.type?.endsWith('Element')||n.type==='Component'||n.type?.endsWith('Block')||n.type==='SnippetBlock'||n.type==='ExpressionTag'||n.type?.endsWith('Directive'))add(n.start,n.end,n.name?'<'+n.name+'>':n.type,n.type==='ExpressionTag'||n.type?.endsWith('Directive')?'binding':'template');},n=>{const kids=[];for(const key of ['nodes','fragment','body','consequent','alternate','fallback','pending','then','catch','attributes']){const v=n[key];if(Array.isArray(v))kids.push(...v);else if(v&&typeof v==='object')kids.push(v);}if(n.type==='Attribute'){const v=n.value;if(Array.isArray(v))kids.push(...v);else if(v&&typeof v==='object')kids.push(v);}return kids;});
 }else if(framework==='angular'){
  const {parseTemplate}=await import('@angular/compiler');
  const parsed=parseTemplate(source,'source.component.html',{preserveWhitespaces:true,preserveLineEndings:true});
  if(parsed.errors?.length){for(const e of parsed.errors)notice(e.msg??e.message??'Μη έγκυρο Angular template.');}
  else{
   walk(parsed.nodes,n=>{const s=n.sourceSpan;if(!s)return;const type=n.constructor.name;if(type==='Text'||type==='TextAttribute'||type==='Variable'||type==='Reference')return false;const label=n.name?'<'+n.name+'>':type;add(s.start.offset,s.end.offset,label,['BoundEvent','BoundAttribute','BoundText'].includes(type)?'binding':'template');},n=>{const kids=[];for(const key of ['children','branches','cases','inputs','outputs','templateAttrs','empty','placeholder','loading','error','mainBlock','blocks']){const v=n[key];if(Array.isArray(v))kids.push(...v);else if(v&&typeof v==='object')kids.push(v);}return kids;});
  }
 }else throw new Error('Unsupported framework');
}catch(e){out.rows=[];out.regions=[];notice(e.message??e);if(e.code||!['CompileError','SyntaxError','Error'].includes(e.name))out.cacheable=false;}
console.log(JSON.stringify(out));
