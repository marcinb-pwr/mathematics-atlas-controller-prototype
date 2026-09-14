import {AtlasGraph, KIND_LABELS, layoutNeighborhood, FORMAL_RELATIONS} from './graph-core.mjs?v=math02';
import {loadAtlas} from './load-atlas.mjs?v=math02';

const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const format = n => n.toLocaleString('en-US');
const el = (tag, cls, text) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const svgEl = (tag, attrs={}) => {const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;};
const compact = (text, max=26) => text.length>max?text.slice(0,max-1)+'…':text;
const colors = {theorem:'#4676d4',proof:'#8554c7',definition:'#087c83',opaque:'#087c83',inductive:'#087c83',axiom:'#b57912',concept:'#479673'};
const labels = {uses:'Proof references',type_ref:'Statement type references',definition_ref:'Definition references',kernel_requires:'Kernel structure requires',proves:'Proves',maps_to:'Concept maps to definition',about_result:'Concept concerns result'};

let graph, atlas, selected, focus, topic='all', branch='all', tab='statement', view, layout;
let transform={x:0,y:0,scale:1}, toastTimer, selectionEpoch=0;

function toast(message){$('toast').textContent=message;$('toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),3200);}
function heading(parent,text){parent.append(el('h3','detail-heading',text));}
function paragraph(parent,text,cls='detail-text'){parent.append(el('p',cls,text));}
function code(parent,text,large=false){parent.append(el('pre',`lean-code${large?' large':''}`,text));}
function button(text,action,cls='text-button'){const b=el('button',cls,text);b.type='button';b.addEventListener('click',action);return b;}
function link(parent,text,url,cls='source-link',download=false){const a=el('a',cls,text);a.href=url;if(download)a.download='';else{a.target='_blank';a.rel='noopener noreferrer';}parent.append(a);return a;}
function declarationDownload(parent,record){
  const a=link(parent,'Declaration JSON','#','',true);
  a.download=record.name.replace(/[^A-Za-z0-9_.-]/g,'_').slice(0,160)+'.json';
  a.addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(record,null,2)+'\n'],{type:'application/json'}));
    a.href=url;setTimeout(()=>URL.revokeObjectURL(url),30000);
  });
}
function disclosures(parent,title,content){const d=el('details'),s=el('summary','',title);d.append(s,content);parent.append(d);return d;}

function renderTopics(){
  $('topics').replaceChildren();
  const topics=atlas.topics.filter(t=>t.branch===branch);
  $('topics').hidden=topics.length<2;
  for(const t of [{id:'all',label:'All topics'},...topics]){
    const b=button(t.label,()=>{topic=t.id;renderTopics();renderResults();},topic===t.id?'active':'');
    b.setAttribute('aria-pressed',String(topic===t.id));$('topics').append(b);
  }
}

function renderBranches(){
  $('branch-filter').replaceChildren();
  for(const b of [{id:'all',label:'All branches',seed_count:atlas.seeds.length},...atlas.branches]){
    const option=el('option','',`${b.label} · ${b.seed_count}`);option.value=b.id;option.selected=branch===b.id;$('branch-filter').append(option);
  }
}

function chooseBranch(id){branch=id;topic='all';renderBranches();renderTopics();renderResults();saveState();}

function renderResults(){
  const q=$('search').value,{total,results}=graph.search(q,topic,80,$('assumption-filter').value,branch);
  $('list-title').textContent=q.trim()?'SEARCH RESULTS':'SELECTED RESULTS';
  $('list-count').textContent=format(total);$('results').replaceChildren();
  if(!results.length)paragraph($('results'),'No matches with these filters. Try another branch, a Lean name, “derivative”, “compact”, or “probability”.','empty-list');
  for(const n of results){
    const b=button('',()=>select(n.id,true),'result-item'+(selected===n.id?' active':''));
    b.setAttribute('aria-pressed',String(selected===n.id));
    b.append(el('span','result-title',n.label),el('span','result-symbol',n.name));
    b.append(el('span','result-meta',q.trim()?`${KIND_LABELS[n.kind]}${n.branches?.length>1?` · ${n.branches.length} branches`:''}`:atlas.branches.find(b=>b.id===n.branch)?.label||''));
    $('results').append(b);
  }
  if(total>results.length)paragraph($('results'),`Showing the first ${results.length} matches. Refine your search to see others.`,'empty-list');
}

function relationships(parent,links,limit=16){
  if(!links.length){paragraph(parent,'No recorded relationships in this category.');return;}
  const list=el('div','relation-list');parent.append(list);let shown=0;
  const more=button('',()=>appendChunk(),'show-more-button');
  function appendChunk(){
    for(const l of links.slice(shown,shown+limit)){
      const n=graph.node(l.id);if(!n)continue;
      const b=button('',()=>select(n.id),'relation-item');
      b.title=`${l.edge?.from||''} ${l.edge?labels[l.edge.relation]:''} ${l.edge?.to||n.name}`;
      const content=el('span');content.append(el('span','relation-symbol',n.name));
      const evidence=l.edge?.evidence;
      content.append(el('span','relation-type',l.edge?`${labels[l.edge.relation]} · ${evidence.kind==='editorial'?'editorial':evidence.expression!==null?`expression #${evidence.expression}`:'kernel structure'}`:KIND_LABELS[n.kind]));
      b.append(el('i',`kind-square ${n.kind}`),content);list.append(b);
    }
    shown=Math.min(links.length,shown+limit);
    more.textContent=`Show more (${links.length-shown} remaining)`;
    more.hidden=shown>=links.length;
  }
  appendChunk();parent.append(more);
}

function renderContext(parent,d,onlyHypotheses=false){
  const binders=d.context.binders.filter(b=>!onlyHypotheses||b.kind==='hypothesis');
  if(!binders.length){paragraph(parent,onlyHypotheses?'No local hypotheses. Variables and type parameters are shown in the statement.':'No outer variables or hypotheses.');return;}
  const ul=el('ul','context-list');
  for(const b of binders){const li=el('li');li.append(el('code','',`${b.name} : ${b.type}`),el('span','context-kind',`${b.kind==='hypothesis'?'Hypothesis':'Parameter'} · ${b.binder.replaceAll('_',' ')}`));ul.append(li);}
  parent.append(ul);
}

async function renderInspector(){
  const epoch=++selectionEpoch,n=graph.node(selected),base=graph.baseNode(selected);
  $('node-kind').textContent=KIND_LABELS[n.kind];$('node-kind').className=`type-badge ${n.kind}`;
  $('node-title').textContent=n.label;$('node-name').textContent=n.kind==='concept'?'Editorial concept':base.name;
  $('node-status').className='node-status'+(n.kind==='axiom'?' assumed':'');
  $('node-status').textContent=n.kind==='concept'?'Curated concept association':n.kind==='axiom'?'Foundational assumption · accepted by the kernel':n.kind==='theorem'||n.kind==='proof'?'✓ Proof rechecked by Lean’s kernel':'✓ Declaration rechecked by Lean’s kernel';
  $('node-branches').replaceChildren();
  const branches=base.branches||[];
  if(branches.length)$('node-branches').append(el('span','shared-label',`Used in ${branches.length} branch${branches.length===1?'':'es'} · dependency closure`));
  for(const id of branches){const b=atlas.branches.find(x=>x.id===id);if(b)$('node-branches').append(button(b.label,()=>chooseBranch(id),'branch-pill'));}
  const links=graph.dependencies(n.id);
  $('dependency-count').textContent=`${format(links.length)} direct links`;
  document.querySelectorAll('[data-tab]').forEach(b=>{const active=b.dataset.tab===tab;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
  const parent=$('inspector-content');parent.replaceChildren();parent.setAttribute('aria-labelledby',`tab-${tab}`);
  if(n.kind==='concept'){
    heading(parent,'Concept');paragraph(parent,n.summary);
    paragraph(parent,'These associations organize the corpus for discovery. They are not proof steps or mathematical implications.');
    heading(parent,'Definitions and selected results');relationships(parent,(graph.out.get(n.id)||[]).map(edge=>({id:edge.to,edge})));
    return;
  }
  let d;try{d=graph.declaration(selected);if(!d)throw new Error('Declaration detail is missing.');}catch(e){if(epoch===selectionEpoch){parent.replaceChildren();paragraph(parent,e.message);parent.append(button('Reload atlas',()=>location.reload()));}return;}
  if(epoch!==selectionEpoch)return;
  parent.replaceChildren();
  if(tab==='statement'){
    if(base.formula){heading(parent,'In mathematical notation');parent.append(el('div','math-statement',base.formula));paragraph(parent,base.summary);paragraph(parent,base.notationNote,'caption');}
    heading(parent,'Exact Lean statement');code(parent,d.statement);
    if(d.docstring){const text=el('p','detail-text',d.docstring);disclosures(parent,'Upstream documentation',text);}
    heading(parent,'Variables & hypotheses');renderContext(parent,d);
    heading(parent,'Statement references');relationships(parent,(graph.out.get(base.id)||[]).filter(e=>e.relation==='type_ref').map(edge=>({id:edge.to,edge})));
  }else if(tab==='proof'){
    if(d.kind==='axiom'){
      heading(parent,'Assumed in this foundation');paragraph(parent,base.summary);paragraph(parent,'An axiom has a checked type and no proof term. Its truth is assumed when declarations use it.');
    }else if(d.value_expression!==null){
      heading(parent,d.kind==='theorem'?'One recorded proof':'Defining expression');
      paragraph(parent,d.kind==='theorem'?'This proof term was rechecked against the statement in a fresh Lean kernel environment. Other proofs may use different dependencies.':'This defining expression was checked in the fresh kernel environment.');
      if(d.source.snippet){heading(parent,'Upstream declaration');code(parent,d.source.snippet,true);if(d.source.url)link(parent,'Open pinned source ↗',d.source.url);}
      const value=el('pre','lean-code large',d.value);disclosures(parent,'Readable elaborated expression',value);
      paragraph(parent,`Exact expression root: #${d.value_expression}. The downloadable expression DAG preserves the kernel term; the rendering above uses Lean’s pretty printer.`,'caption');
      heading(parent,'References in this expression');
      const id=d.kind==='theorem'?graph.proofOf(base.id):base.id;
      relationships(parent,(graph.out.get(id)||[]).filter(e=>e.relation==='uses'||e.relation==='definition_ref').map(edge=>({id:edge.to,edge})));
    }else{
      heading(parent,'Kernel-generated declaration');paragraph(parent,'This object has no ordinary value expression. Its type, inductive metadata, and any computation rules are retained in the export. Constructors and recursors were compared with those generated by the kernel.');
      code(parent,JSON.stringify(d.kernel_metadata,null,2));
      if(d.recursor_rules.length)code(parent,JSON.stringify(d.recursor_rules,null,2));
    }
  }else if(tab==='axioms'){
    heading(parent,'Local hypotheses');renderContext(parent,d,true);
    heading(parent,'Foundational axioms');
    const box=el('div','assumption-box');box.append(el('strong','',`${d.axioms.length} named axiom${d.axioms.length===1?'':'s'} in this declaration’s closure`));
    box.append(el('p','',d.axioms.length?'These are the axioms reached by Lean’s axiom collector for this particular declaration and its supporting proofs.':'Lean’s axiom collector reaches no named axioms. The declaration still relies on the rules of Lean’s underlying type theory.'));parent.append(box);
    if(d.axioms.length){relationships(parent,d.axioms.map(id=>({id})));parent.append(button('Trace recorded paths to these axioms',()=>{focus=selected;$('view-mode').value='axioms';renderGraph();saveState();},'show-more-button'));}
    paragraph(parent,'This list is a report about the recorded formalization, not a proof that each axiom is mathematically necessary. Logical rules, inductive types, and kernel primitives are also part of the foundation.');
  }else{
    heading(parent,'Pinned source');
    paragraph(parent,d.module||'Kernel-generated declaration','source-path');
    if(d.source.url){link(parent,'Read declaration on GitHub ↗',d.source.url);paragraph(parent,d.source.path,'source-path');paragraph(parent,`Revision ${d.source.revision}`,'source-path');}else paragraph(parent,'A source range is unavailable for this generated declaration. Its exported type and kernel metadata remain available.');
    if(d.source.snippet){code(parent,d.source.snippet,true);paragraph(parent,d.source.range_note,'caption');}
    heading(parent,'Verification');paragraph(parent,`Lean ${atlas.metadata.snapshot.lean_version} · trust level 0 · kernel replay passed.`);
    heading(parent,'Exact expression identifiers');code(parent,`type_expression: ${d.type_expression}\nvalue_expression: ${d.value_expression??'none'}\nuniverse_parameters: ${JSON.stringify(d.level_parameters)}`);
    heading(parent,'Snapshot identity');code(parent,base.sourceId);
    heading(parent,'Classification');
    paragraph(parent,d.subject_labels?.length?`Subject labels: ${d.subject_labels.join(', ')}. Review: ${d.label_provenance.subject_labels.review_status.replaceAll('_',' ')}.`:'Subject labels have not been assigned.');
    paragraph(parent,'Branch badges show where this declaration is used in the selected results’ dependency closures. They are separate from subject labels.','caption');
    if(d.generated===true)paragraph(parent,'Suspected generated helper, identified by its Lean name. Excluded from benchmark candidates.','caption');
    const actions=el('div','download-actions');declarationDownload(actions,d);link(actions,'Complete dataset','data/mathematics-atlas-dataset.zip','',true);parent.append(actions);
    paragraph(parent,'The declaration JSON contains this object, its pinned source, and its exact expression identifiers.','caption');
  }
}

async function select(id,center=false){
  if(!graph.node(id))throw new Error(`Unknown node: ${id}`);
  selected=id;
  if(graph.node(id).kind==='proof'&&tab==='statement')tab='proof';
  if(center){focus=id;$('workspace').classList.remove('library-open');if(graph.node(id).kind==='concept')$('view-mode').value='concepts';else if($('view-mode').value==='concepts')$('view-mode').value='dependencies';renderGraph();}
  else highlightSelection();
  renderResults();saveState();await renderInspector();
}

function saveState(){const hash=new URLSearchParams({node:selected,focus,branch,view:$('view-mode').value,depth:$('depth').value,kernel:$('show-kernel').checked?'1':'0'});history.replaceState(null,'',`#${hash}`);}
function setTab(next){tab=next;renderInspector();}

function renderGraph(){
  const n=graph.node(focus),mode=$('view-mode').value;
  $('graph-eyebrow').textContent=mode==='concepts'?'CURATED CONCEPT MAP':mode==='axioms'?'RECORDED AXIOM PATHS':mode==='dependents'?'USAGE NEIGHBORHOOD':'PROOF NEIGHBORHOOD';
  $('graph-title').textContent=n.label;$('graph-subtitle').textContent=n.name;
  $('depth-control').hidden=$('kernel-control').hidden=['axioms','concepts'].includes(mode);
  view=graph.neighborhood(focus,{mode,depth:Number($('depth').value),showKernel:$('show-kernel').checked,limit:70});
  layout=layoutNeighborhood(view,focus);
  const world=$('graph-world');world.replaceChildren();
  const edgeLayer=svgEl('g'),nodeLayer=svgEl('g');world.append(edgeLayer,nodeLayer);
  for(const e of view.edges){
    // Formal arrows point from a supporting object toward the object it supports.
    // Concept arrows follow their editorial association from concept to result.
    const reverse=FORMAL_RELATIONS.has(e.relation),a=layout.positions.get(reverse?e.to:e.from),b=layout.positions.get(reverse?e.from:e.to);
    if(!a||!b)continue;
    const right=b.x>=a.x,ax=a.x+(right?a.width:0),bx=b.x+(right?0:b.width),ay=a.y+a.height/2,by=b.y+b.height/2;
    const bend=Math.max(38,Math.abs(bx-ax)/2);
    const path=svgEl('path',{d:`M ${ax} ${ay} C ${ax+(right?bend:-bend)} ${ay}, ${bx+(right?-bend:bend)} ${by}, ${bx} ${by}`,class:'graph-edge','data-from':e.from,'data-to':e.to});
    const title=svgEl('title');title.textContent=`${e.from}\n${labels[e.relation]} → ${e.to}\nEvidence: ${e.evidence.kind}${e.evidence.expression!==null&&e.evidence.expression!==undefined?` · expression #${e.evidence.expression}`:''}`;path.append(title);edgeLayer.append(path);
  }
  for(const n of view.nodes){
    const p=layout.positions.get(n.id),g=svgEl('g',{transform:`translate(${p.x},${p.y})`,class:`graph-node ${n.kind}`,'data-id':n.id,tabindex:0,role:'button','aria-label':`${KIND_LABELS[n.kind]}: ${n.name}. Enter to inspect; Shift Enter to focus.`});
    g.append(svgEl('rect',{width:p.width,height:p.height,rx:7,class:'node-box'}));
    g.append(svgEl('rect',{x:0,y:17,width:3,height:38,rx:1.5,fill:colors[n.kind]||'#91a2b9'}));
    const kind=svgEl('text',{x:14,y:19,class:'node-kind-label'});kind.textContent=KIND_LABELS[n.kind].toUpperCase();kind.style.fill=colors[n.kind]||'#718299';g.append(kind);
    const label=svgEl('text',{x:14,y:40});label.textContent=compact(n.kind==='proof'?`Proof · ${graph.baseNode(n.id).label}`:n.label,27);g.append(label);
    const symbol=svgEl('text',{x:14,y:59,class:'node-symbol'});symbol.textContent=compact(graph.baseNode(n.id).name,30);g.append(symbol);
    const title=svgEl('title');title.textContent=`${n.label}\n${n.name}\nClick to inspect · double-click to focus`;g.append(title);
    g.addEventListener('click',()=>{if(!suppressClick)select(n.id);});g.addEventListener('dblclick',()=>select(n.id,true));
    g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(n.id,e.shiftKey);}});nodeLayer.append(g);
  }
  const notes=[`${view.nodes.length} nodes · ${view.edges.length} links`];
  if(view.hidden)notes.push(`${view.hidden} links to kernel details hidden`);
  if(view.omitted)notes.push(`${view.omitted} links beyond the view limit`);
  notes.push(mode==='concepts'?'Editorial associations':mode==='axioms'?'One recorded path per named axiom':'Arrows lead toward the supported result');
  notes.push('Double-click a node to explore');
  $('graph-note').textContent=notes.join(' · ');
  $('graph-empty').hidden=view.nodes.length!==1;
  $('graph-empty').textContent=mode==='axioms'?'No named axioms are reached. Lean’s underlying type theory still applies.':mode==='dependents'?'No uses in this extracted corpus.':'No additional visible links. Try “Kernel details” or another view.';
  highlightSelection();requestAnimationFrame(fitGraph);
}

function highlightSelection(){
  document.querySelectorAll('.graph-node').forEach(n=>{n.classList.toggle('selected',n.dataset.id===selected);n.classList.toggle('focused',n.dataset.id===focus);});
  document.querySelectorAll('.graph-edge').forEach(e=>e.classList.toggle('highlight',e.dataset.from===selected||e.dataset.to===selected));
}
function applyTransform(){const {x,y,scale}=transform;$('graph-world').setAttribute('transform',`translate(${x},${y}) scale(${scale})`);$('zoom-level').textContent=`${Math.round(scale*100)}%`;}
function fitGraph(){
  if(!layout)return;const w=$('graph-stage').clientWidth,h=$('graph-stage').clientHeight;
  const scale=Math.min(1.05,(w-68)/Math.max(1,layout.width),(h-95)/Math.max(1,layout.height));
  transform={scale:Math.max(.12,scale),x:(w-layout.width*scale)/2,y:Math.max(25,(h-45-layout.height*scale)/2)};applyTransform();
}
function zoom(factor,cx=$('graph-stage').clientWidth/2,cy=$('graph-stage').clientHeight/2){const old=transform.scale,next=Math.max(.12,Math.min(2.2,old*factor));transform.x=cx-(cx-transform.x)*next/old;transform.y=cy-(cy-transform.y)*next/old;transform.scale=next;applyTransform();}
let drag=null,suppressClick=false;
function bindGraphNavigation(){
  const g=$('graph');
  g.addEventListener('wheel',e=>{e.preventDefault();const r=g.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.0015),e.clientX-r.left,e.clientY-r.top);},{passive:false});
  g.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,tx:transform.x,ty:transform.y,moved:false,id:e.pointerId};suppressClick=false;});
  g.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>5){drag.moved=true;g.setPointerCapture(e.pointerId);g.classList.add('dragging');}if(drag.moved){transform.x=drag.tx+dx;transform.y=drag.ty+dy;applyTransform();}});
  const end=e=>{if(!drag||e.pointerId!==drag.id)return;suppressClick=drag.moved;drag=null;g.classList.remove('dragging');if(g.hasPointerCapture(e.pointerId))g.releasePointerCapture(e.pointerId);setTimeout(()=>{suppressClick=false;},0);};
  g.addEventListener('pointerup',end);g.addEventListener('pointercancel',end);
  $('zoom-in').addEventListener('click',()=>zoom(1.25));$('zoom-out').addEventListener('click',()=>zoom(.8));$('fit-graph').addEventListener('click',fitGraph);
  new ResizeObserver(()=>{if(layout)fitGraph();}).observe($('graph-stage'));
}

function renderReceipt(){
  const r=atlas.metadata,parent=$('dataset-content');parent.replaceChildren();
  paragraph(parent,`${atlas.seeds.length} selected results across ${atlas.branches.length} branches, with their complete recorded dependency closure. Shared definitions, lemmas, and axioms appear once.`);
  const stats=el('div','receipt-stats');
  for(const [number,label]of [[r.declaration_count,'formal declarations'],[r.proof_count,'rechecked proofs'],[r.edge_count,'recorded links']]){const stat=el('div','receipt-stat');stat.append(el('strong','',format(number)),el('span','',label));stats.append(stat);}parent.append(stats);
  const dl=el('dl');
  for(const [key,value]of [['Kernel check',`Passed · trust level ${r.kernel_trust_level}`],['Reference audit','Passed · 0 unresolved references'],['Axiom audit','Passed · independent agreement with Lean'],['Admitted proofs',String(r.admitted_proofs)],['Foundation',`Lean ${r.snapshot.lean_version} · dependent type theory`],['Mathlib snapshot',r.snapshot.mathlib_commit],['Exact expressions',format(r.expression_count)],['Source ranges',`${format(r.source_snippet_count)} declarations with snippets`],['Built',new Date(r.generated_at).toISOString().slice(0,10)]]){const row=el('div','receipt-row');row.append(el('dt','',key),el('dd','',value));dl.append(row);}parent.append(dl);
  heading(parent,'Branches and shared symbols');
  paragraph(parent,`${format(r.shared_declaration_count)} formal declarations are reused by more than one branch. The same qualified Lean name resolves to the same node, proof, and source record throughout the atlas.`,'receipt-note');
  const branchList=el('dl');for(const b of atlas.branches){const row=el('div','receipt-row');row.append(el('dt','',b.label),el('dd','',`${b.seed_count} entry points · ${format(b.declaration_count)} declarations including shared dependencies`));branchList.append(row);}parent.append(branchList);
  heading(parent,'Scope and interpretation');for(const text of r.limitations)paragraph(parent,text,'receipt-note');
  heading(parent,'Get the data');const actions=el('div','download-actions');
  link(actions,'Full graph + exact expressions','data/mathematics-atlas-dataset.zip','',true);link(actions,'Graph JSON (expression references)','data/atlas.json.gz','',true);link(actions,'Exact expressions (gzip)','data/expressions.ndjson.gz','',true);link(actions,'Extraction + replay source','data/mathematics-atlas-source.zip','',true);link(actions,'Verification receipt','data/verification.json','',true);parent.append(actions);
  paragraph(parent,'The full dataset ZIP contains the exact expression table. The graph-only JSON references that table; use the separate expression download when loading it outside this explorer.','receipt-note');
  heading(parent,'Replayable proof pilot');
  paragraph(parent,'Nat.Prime.dvd_mul: 14 executed actions, 4 failures and 15 recorded states, including a discarded branch. The environment is rebuilt before the target declaration. Every state and outcome matched a fresh Lean replay, and the standalone final proof passed.','receipt-note');
  const pilotActions=el('div','download-actions');link(pilotActions,'Download proof episode','data/mathematics-atlas-replay-pilot.zip','',true);link(pilotActions,'Training package manifest','data/training-manifest.json','',true);parent.append(pilotActions);
  paragraph(parent,'This hand-authored episode validates recording and replay. It is not a trained controller or a held-out benchmark. Subject labels, branch usage, generated-name evidence and target-screening metadata are stored separately.','receipt-note');
  link(parent,'Replay contents and reproduction','data/replay-pilot/README.md');
  link(parent,'Schema & relationship semantics','data/SCHEMA.md');link(parent,'Attribution & licenses','data/NOTICE.md');
}

function setupActions(){
  $('search').addEventListener('input',renderResults);
  $('branch-filter').addEventListener('change',()=>chooseBranch($('branch-filter').value));
  $('assumption-filter').addEventListener('change',renderResults);
  $('reset-selection').addEventListener('click',()=>{branch='number-theory';topic='all';$('search').value='';$('assumption-filter').value='any';$('view-mode').value='dependencies';$('depth').value='1';$('show-kernel').checked=false;tab='statement';renderBranches();renderTopics();select(atlas.seeds[0],true);});
  for(const id of ['view-mode','depth','show-kernel'])$(id).addEventListener('change',()=>{renderGraph();saveState();});
  for(const id of ['focus-selected','inspect-focus'])$(id).addEventListener('click',()=>select(selected,true));
  $('toggle-library').addEventListener('click',()=>{const open=$('workspace').classList.toggle('library-open');$('toggle-library').setAttribute('aria-expanded',String(open));if(open)$('search').focus();});
  $('copy-name').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(graph.baseNode(selected).name);toast('Name copied.');}catch{toast('Copy unavailable. Select the qualified name to copy it.');}});
  const tabs=[...document.querySelectorAll('[data-tab]')];
  tabs.forEach((b,i)=>{b.addEventListener('click',()=>setTab(b.dataset.tab));b.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();setTab(tabs[next].dataset.tab);});});
  $('open-dataset').addEventListener('click',()=>$('dataset-dialog').showModal());$('close-dataset').addEventListener('click',()=>$('dataset-dialog').close());
  $('dataset-dialog').addEventListener('click',e=>{if(e.target===$('dataset-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('dataset-dialog').open){e.preventDefault();$('workspace').classList.add('library-open');$('search').focus();}});
  bindGraphNavigation();
}

function registerTools(){
  const context=document.modelContext;if(!context?.registerTool)return;
  const lifecycle=new AbortController();
  const register=tool=>{try{Promise.resolve(context.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}};
  register({name:'search_mathematics_atlas',title:'Search mathematical declarations',description:'Search the currently loaded mathematical corpus without changing the interface.',inputSchema:{type:'object',properties:{query:{type:'string'},limit:{type:'integer',minimum:1,maximum:40}},required:['query'],additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:false},execute(input){if(!input||typeof input.query!=='string'||(input.limit!==undefined&&(!Number.isInteger(input.limit)||input.limit<1||input.limit>40)))throw new Error('Expected query text and an optional limit from 1 to 40.');const r=graph.search(input.query,'all',input.limit||20);return{total:r.total,results:r.results.map(n=>({id:n.id,label:n.label,kind:n.kind,axioms:n.axioms}))};}});
  register({name:'navigate_mathematics_atlas',title:'Inspect a mathematical object',description:'Focus a known node in the visible graph and open its statement, proof, assumptions, or source tab.',inputSchema:{type:'object',properties:{nodeId:{type:'string'},tab:{type:'string',enum:['statement','proof','axioms','source']}},required:['nodeId'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},async execute(input){if(!input||typeof input.nodeId!=='string'||!graph.node(input.nodeId)||input.tab&&!['statement','proof','axioms','source'].includes(input.tab))throw new Error('Provide an existing nodeId and a valid tab.');tab=input.tab||'statement';await select(input.nodeId,true);await new Promise(requestAnimationFrame);return{selected,focus,tab,nodeCount:view.nodes.length,axioms:graph.node(selected).axioms};}});
  window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}

async function boot(){
  atlas=await loadAtlas();graph=new AtlasGraph(atlas);
  const state=new URLSearchParams(location.hash.slice(1));selected=graph.node(state.get('node'))?state.get('node'):atlas.seeds[0];focus=graph.node(state.get('focus'))?state.get('focus'):selected;
  if(atlas.branches.some(b=>b.id===state.get('branch')))branch=state.get('branch');
  if(['dependencies','dependents','axioms','concepts'].includes(state.get('view')))$('view-mode').value=state.get('view');
  if(['1','2','3','4'].includes(state.get('depth')))$('depth').value=state.get('depth');$('show-kernel').checked=state.get('kernel')==='1';
  $('seed-count').textContent=atlas.seeds.length;$('lean-version').textContent=atlas.metadata.snapshot.lean_version;$('commit-short').textContent=atlas.metadata.snapshot.mathlib_commit.slice(0,7);
  $('edition').textContent=atlas.metadata.edition;$('corpus-label').textContent=`${atlas.branches.length} branches · shared foundations`;
  $('workspace').hidden=false;$('loading').hidden=true;renderBranches();renderTopics();renderResults();renderReceipt();setupActions();renderGraph();await renderInspector();saveState();registerTools();
}

boot().catch(error=>{const p=$('loading');p.hidden=false;p.className='error-panel';p.replaceChildren(el('h1','','The atlas could not open.'),el('p','',error.message),button('Reload',()=>location.reload()));$('workspace').hidden=true;});
