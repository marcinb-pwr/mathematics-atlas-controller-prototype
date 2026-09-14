import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import {AtlasGraph, FORMAL_RELATIONS, layoutNeighborhood} from '../dist/graph-core.mjs';

const data=JSON.parse(gunzipSync(readFileSync(new URL('../dist/data/atlas.json.gz',import.meta.url))).toString());
const graph=new AtlasGraph(data);

test('every edge resolves, carries evidence, and belongs to the exported corpus',()=>{
  assert.equal(graph.nodes.size,data.nodes.length);
  for(const e of data.edges){assert(graph.node(e.from));assert(graph.node(e.to));assert(e.evidence);}
  assert.equal(data.metadata.unresolved_references,0);
  assert.equal(data.metadata.kernel_replay,'passed');
  assert.equal(data.metadata.kernel_trust_level,0);
  assert.equal(data.metadata.independent_axiom_audit,'passed');
});

test('every formal statement has a separate, inspectable proof node',()=>{
  for(const n of data.nodes.filter(n=>n.kind==='theorem')){
    const proof=graph.proofOf(n.id);assert(proof);assert.equal(graph.baseNode(proof).id,n.id);
    assert.deepEqual(graph.node(proof).axioms,n.axioms);
    assert(graph.dependencies(n.id).some(l=>l.id===proof&&l.edge.relation==='proves'));
    assert(graph.dependents(proof).some(l=>l.id===n.id));
  }
});

test('every seed has a recorded path to each reported axiom',()=>{
  for(const seed of data.seeds){
    const v=graph.neighborhood(seed,{mode:'axioms'});
    for(const ax of graph.node(seed).axioms){
      assert(v.nodes.some(n=>n.id===ax),`${seed} must reach ${ax}`);
      const path=graph.shortestDependencyPath(seed,ax);assert(path);
      assert(path.edges.every(e=>FORMAL_RELATIONS.has(e.relation)||e.relation==='proves'));
    }
  }
  assert.equal(graph.neighborhood('Nat.ModEq.refl',{mode:'axioms'}).nodes.length,1);
});

test('assumption filters distinguish proof choices without changing the corpus',()=>{
  const none=graph.search('','all',80,'none').results;
  assert(none.some(n=>n.id==='Nat.ModEq.refl'));
  assert(none.every(n=>n.axioms.length===0));
  const choice=graph.search('','all',80,'choice').results;
  assert(choice.some(n=>n.id==='Nat.exists_infinite_primes'));
  const without=graph.search('','all',80,'no-choice').results;
  assert(without.every(n=>!n.axioms.includes('Classical.choice')));
  assert.equal(choice.length+without.length,data.seeds.length);
});

test('bounded neighborhood views retain only real edges and valid positions',()=>{
  const all=new Map(data.edges.map(e=>[e.id,e]));
  for(const mode of ['dependencies','dependents','axioms','concepts']){
    for(const root of data.seeds){
      const v=graph.neighborhood(root,{mode,depth:4,showKernel:true,limit:20});
      assert(v.nodes.length<=20);const ids=new Set(v.nodes.map(n=>n.id));
      for(const e of v.edges){assert.equal(all.get(e.id),e);assert(ids.has(e.from)&&ids.has(e.to));}
      const layout=layoutNeighborhood(v,root);
      for(const n of v.nodes){const p=layout.positions.get(n.id);assert(p);assert(Number.isFinite(p.x)&&Number.isFinite(p.y));}
    }
  }
});

test('editorial concepts never become formal proof edges',()=>{
  for(const c of graph.concepts){assert.equal(graph.dependencies(c.id).length,0);assert.equal(graph.dependents(c.id).length,0);}
  const results=graph.search('Nat.gcd_comm').results;
  assert.equal(results[0].id,'Nat.gcd_comm');
});

test('a corrupted edge is rejected instead of becoming an invented node',()=>{
  const corrupt={...data,edges:[...data.edges,{id:'invalid',from:data.seeds[0],to:'missing:declaration',relation:'uses'}]};
  assert.throws(()=>new AtlasGraph(corrupt),/Unresolved graph edge/);
  assert.throws(()=>graph.neighborhood('missing:declaration'),/Unknown graph root/);
});

test('all branches expose their curated entry points without duplicating common symbols',()=>{
  assert.equal(data.branches.length,8);
  assert.equal(data.seeds.length,33);
  for(const b of data.branches){
    const results=graph.search('','all',100,'any',b.id).results;
    assert.equal(results.length,b.seed_count);
    assert(results.every(n=>n.branch===b.id));
  }
  const shared=graph.node('Set.ext');
  for(const branch of ['logic-sets','analysis','topology']){
    assert(shared.branches.includes(branch));
    assert.equal(graph.search('Set.ext','all',100,'any',branch).results.find(n=>n.id==='Set.ext'),shared);
  }
  assert.equal(data.nodes.filter(n=>n.id==='Set.ext').length,1);
  assert.equal(graph.declaration(graph.proofOf('Set.ext')),graph.declaration('Set.ext'));
  assert(data.metadata.shared_declaration_count>0);
});
