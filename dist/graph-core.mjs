/** Graph operations use only recorded relationships. They never infer new proofs. */
export const FORMAL_RELATIONS = new Set(['uses', 'type_ref', 'definition_ref', 'kernel_requires']);
export const KIND_LABELS = {theorem:'Statement',proof:'Proof',definition:'Definition',axiom:'Axiom',inductive:'Inductive type',constructor:'Constructor',recursor:'Recursor',quotient_primitive:'Kernel primitive',opaque:'Opaque definition',concept:'Concept'};

export class AtlasGraph {
  constructor(data) {
    this.data = data;
    this.nodes = new Map(data.nodes.map(n => [n.id, n]));
    if (this.nodes.size !== data.nodes.length) throw new Error('Duplicate canonical declaration IDs in this atlas.');
    // Open the graph only when all inspector records came with the same payload.
    // Node selection must never depend on another request succeeding.
    for (const n of data.nodes) if (n.kind !== 'concept') this.declaration(n.id);
    this.out = new Map(); this.inc = new Map();
    for (const e of data.edges) {
      if (!this.nodes.has(e.from) || !this.nodes.has(e.to)) throw new Error(`Unresolved graph edge: ${e.from} → ${e.to}`);
      if (!this.out.has(e.from)) this.out.set(e.from, []);
      if (!this.inc.has(e.to)) this.inc.set(e.to, []);
      this.out.get(e.from).push(e); this.inc.get(e.to).push(e);
    }
    this.seeds = data.seeds.map(id => this.nodes.get(id)).filter(Boolean);
    this.concepts = data.nodes.filter(n => n.kind === 'concept');
  }
  node(id) { return this.nodes.get(id); }
  proofOf(id) { return (this.inc.get(id) || []).find(e => e.relation === 'proves')?.from; }
  baseNode(id) { const n = this.node(id); return n?.kind === 'proof' ? this.node(n.statementId) : n; }
  declaration(id) {
    const n = this.baseNode(id);
    if (!n || n.kind === 'concept') return null;
    const records = this.data.declarations;
    if (!records || !Object.hasOwn(records, n.id) || records[n.id]?.name !== n.id) {
      throw new Error('This copy of the atlas is incomplete. Reload to download it again.');
    }
    return records[n.id];
  }
  dependencies(id) {
    const links = (this.out.get(id) || []).filter(e => FORMAL_RELATIONS.has(e.relation)).map(e => ({id:e.to,edge:e}));
    for (const e of this.inc.get(id) || []) if (e.relation === 'proves') links.unshift({id:e.from,edge:e});
    return links.filter(x => x.id !== id);
  }
  dependents(id) {
    const links = (this.inc.get(id) || []).filter(e => FORMAL_RELATIONS.has(e.relation)).map(e => ({id:e.from,edge:e}));
    for (const e of this.out.get(id) || []) if (e.relation === 'proves') links.push({id:e.to,edge:e});
    return links.filter(x => x.id !== id);
  }
  search(query, topic='all', limit=80, assumption='any', branch='all') {
    const q = query.trim().toLowerCase();
    const candidates = q ? this.data.nodes.filter(n => n.kind !== 'proof') : this.seeds;
    const ranked = candidates.filter(n => (branch === 'all' || (q ? n.branches?.includes(branch) : n.branch === branch)) && (topic === 'all' || n.topics?.includes(topic)) && (assumption==='any' || n.kind!=='concept' && (assumption==='none' ? n.axioms.length===0 : assumption==='no-choice' ? !n.axioms.includes('Classical.choice') : assumption==='choice' ? n.axioms.includes('Classical.choice') : false))).map(n => {
      const haystack = `${n.name} ${n.label || ''} ${n.summary || ''}`.toLowerCase();
      if (q && !haystack.includes(q)) return null;
      const score = !q ? this.data.seeds.indexOf(n.id) : n.name.toLowerCase() === q ? -100 : n.seed ? -20 : n.kind === 'concept' ? -10 : n.technical ? 50 : 0;
      return {n,score};
    }).filter(Boolean).sort((a,b) => a.score-b.score || a.n.name.localeCompare(b.n.name));
    return {total:ranked.length,results:ranked.slice(0,limit).map(x=>x.n)};
  }
  neighborhood(root, {depth=2,mode='dependencies',showKernel=false,limit=90}={}) {
    if (!this.node(root)) throw new Error('Unknown graph root');
    if (mode === 'axioms') return this.axiomPaths(root,limit);
    if (mode === 'concepts') return this.conceptMap(root,limit);
    const getLinks = mode === 'dependents' ? id=>this.dependents(id) : id=>this.dependencies(id);
    const distances = new Map([[root,0]]); const queue = [root]; const edges = new Map();
    let hidden = 0, omitted = 0;
    for (let qi=0;qi<queue.length;qi++) {
      const id=queue[qi], d=distances.get(id);
      if (d >= depth) continue;
      const links=getLinks(id).sort((a,b)=>(this.node(a.id).technical?1:0)-(this.node(b.id).technical?1:0) || (this.node(a.id).kind==='proof'?-1:0)-(this.node(b.id).kind==='proof'?-1:0) || a.id.localeCompare(b.id));
      for (const link of links) {
        const n=this.node(link.id);
        if (!showKernel && n.technical && link.id !== root) {hidden++;continue;}
        if (!distances.has(link.id)) {
          if (distances.size >= limit) {omitted++;continue;}
          distances.set(link.id,d+1);queue.push(link.id);
        }
        edges.set(link.edge.id,link.edge);
      }
    }
    return {nodes:queue.map(id=>this.node(id)),edges:[...edges.values()],distances,hidden,omitted,mode};
  }
  shortestDependencyPath(root,target) {
    const parents=new Map([[root,null]]),queue=[root];
    for(let i=0;i<queue.length;i++){
      const id=queue[i]; if(id===target)break;
      for(const link of this.dependencies(id))if(!parents.has(link.id)){parents.set(link.id,{id,edge:link.edge});queue.push(link.id);}
    }
    if(!parents.has(target))return null;
    const nodes=[target],edges=[];let cur=target;
    while(parents.get(cur)){const p=parents.get(cur);edges.push(p.edge);nodes.push(p.id);cur=p.id;}
    return {nodes:nodes.reverse(),edges:edges.reverse()};
  }
  axiomPaths(root,limit=120) {
    const n=this.node(root), ids=new Set([root]),edges=new Map();
    for(const ax of n.axioms||[]){const path=this.shortestDependencyPath(root,ax);if(!path)continue;for(const id of path.nodes)ids.add(id);for(const e of path.edges)edges.set(e.id,e);}
    const distances=new Map([[root,0]]),queue=[root];
    for(let i=0;i<queue.length;i++)for(const l of this.dependencies(queue[i]))if(ids.has(l.id)&&edges.has(l.edge.id)&&!distances.has(l.id)){distances.set(l.id,distances.get(queue[i])+1);queue.push(l.id);}
    const chosen=[...ids].slice(0,limit),keep=new Set(chosen);
    return {nodes:chosen.map(id=>this.node(id)),edges:[...edges.values()].filter(e=>keep.has(e.from)&&keep.has(e.to)),distances,hidden:0,omitted:Math.max(0,ids.size-limit),mode:'axioms'};
  }
  conceptMap(root,limit=90) {
    const base=this.baseNode(root), topics=base?.kind==='concept'?[base.topic]:(base?.topics||[]);
    const concepts=this.concepts.filter(n=>topics.length?topics.includes(n.topic):true);
    const ids=new Set(concepts.map(n=>n.id)),edges=[];
    if(base)ids.add(base.id);
    for(const c of concepts)for(const e of this.out.get(c.id)||[]){if(e.relation==='maps_to'||e.relation==='about_result'){ids.add(e.to);edges.push(e);}}
    const chosen=[...ids].slice(0,limit),keep=new Set(chosen);
    const distances=new Map(chosen.map(id=>[id,this.node(id).kind==='concept'?1:0]));
    return {nodes:chosen.map(id=>this.node(id)),edges:edges.filter(e=>keep.has(e.from)&&keep.has(e.to)),distances,hidden:0,omitted:Math.max(0,ids.size-limit),mode:'concepts'};
  }
}

export function layoutNeighborhood(view,root) {
  const maxDepth=Math.max(0,...view.nodes.map(n=>view.distances.get(n.id)||0));
  const layers=new Map();
  for(const n of view.nodes){const d=view.distances.get(n.id)||0;if(!layers.has(d))layers.set(d,[]);layers.get(d).push(n);}
  const gap=96, width=232, column=290;
  const tallest=Math.max(1,...[...layers.values()].map(a=>a.length))*gap;
  const positions=new Map();
  for(const [d,ns]of layers){ns.sort((a,b)=>(a.kind==='proof'?-1:0)-(b.kind==='proof'?-1:0)||a.name.localeCompare(b.name));
    const x=view.mode==='dependents'?d*column:(maxDepth-d)*column;
    for(let i=0;i<ns.length;i++)positions.set(ns[i].id,{x,y:(tallest-ns.length*gap)/2+i*gap,width,height:72});
  }
  return {positions,width:maxDepth*column+width,height:tallest-24,root};
}
