import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync, gzipSync} from 'node:zlib';
import {AtlasGraph} from '../dist/graph-core.mjs';
import {loadAtlas, ATLAS_DATA_URL} from '../dist/load-atlas.mjs';

const data=JSON.parse(gunzipSync(readFileSync(new URL('../dist/data/atlas.json.gz',import.meta.url))).toString());

test('every node and proof can be inspected without network access',t=>{
  const network=t.mock.method(globalThis,'fetch',()=>{throw new Error('Network unavailable');});
  const graph=new AtlasGraph(data),shards=new Map();
  for(const n of data.nodes){
    const record=graph.declaration(n.id);
    if(n.kind==='concept'){assert.equal(record,null);continue;}
    if(!shards.has(n.detailShard))shards.set(n.detailShard,JSON.parse(readFileSync(new URL(`../dist/data/${n.detailShard}`,import.meta.url),'utf8')));
    const original=shards.get(n.detailShard)[n.statementId||n.id];
    assert.deepEqual(record,original,`Unchanged details for ${n.id}`);
  }
  assert.equal(network.mock.callCount(),0);
  assert.equal(Object.keys(data.declarations).length,data.metadata.declaration_count);
});

test('a stale or incomplete payload is rejected before the explorer opens',()=>{
  assert.throws(()=>new AtlasGraph({...data,declarations:undefined}),/incomplete.*Reload/);
  const records={...data.declarations};delete records[data.seeds[0]];
  assert.throws(()=>new AtlasGraph({...data,declarations:records}),/incomplete.*Reload/);
});

test('a transient initial download failure recovers automatically',async()=>{
  const requests=[],waits=[];
  const result=await loadAtlas({fetchImpl:async(url,options)=>{
    requests.push({url,options});
    if(requests.length===1)return new Response('',{status:503});
    if(requests.length===2)throw new TypeError('Failed to fetch');
    return Response.json({ok:true});
  },wait:async ms=>{waits.push(ms);}});
  assert.deepEqual(result,{ok:true});assert.equal(requests.length,3);
  assert(requests.every(r=>r.url===ATLAS_DATA_URL&&r.options.credentials==='same-origin'));
  assert.deepEqual(requests.map(r=>r.options.cache),['default','reload','reload']);
  assert.deepEqual(waits,[250,500]);
});

test('authorization failures do not retry protected resources',async()=>{
  for(const status of [401,403]){
    let calls=0;
    await assert.rejects(loadAtlas({fetchImpl:async()=>{calls++;return new Response('',{status});},wait:async()=>{assert.fail('Must not retry');}}),/sign in again/);
    assert.equal(calls,1);
  }
});

test('persistent failures stop after a bounded number of requests',async()=>{
  let calls=0;
  await assert.rejects(loadAtlas({fetchImpl:async()=>{calls++;throw new TypeError('Offline');},wait:async()=>{}}),/Check your connection/);
  assert.equal(calls,3);
});

test('an expired-session HTML page is not accepted as graph data',async()=>{
  let calls=0;
  await assert.rejects(loadAtlas({fetchImpl:async()=>{calls++;return new Response('<html>Sign in</html>',{headers:{'Content-Type':'text/html'}});},wait:async()=>{assert.fail('Must not retry');}}),/page instead of graph data/);
  assert.equal(calls,1);
});

test('the compressed multi-branch payload loads in one request',async()=>{
  let calls=0;
  const result=await loadAtlas({fetchImpl:async()=>{
    calls++;
    return new Response(readFileSync(new URL('../dist/data/atlas.json.gz',import.meta.url)),{headers:{'Content-Type':'application/gzip'}});
  }});
  assert.equal(calls,1);
  assert.equal(Object.keys(result.declarations).length,data.metadata.declaration_count);
  assert.equal(result.branches.length,8);
  assert.deepEqual(result.declarations['Set.ext'],data.declarations['Set.ext']);
});

test('a host-decoded gzip response is not decompressed twice',async()=>{
  const result=await loadAtlas({fetchImpl:async()=>new Response('{"ok":true}',{
    headers:{'Content-Encoding':'gzip','Content-Type':'application/json'},
  })});
  assert.deepEqual(result,{ok:true});
});

test('an incomplete compressed transfer is retried',async()=>{
  const payload=gzipSync('{"ok":true}');let calls=0;
  const result=await loadAtlas({fetchImpl:async()=>{
    calls++;
    return new Response(calls===1?payload.subarray(0,payload.length-5):payload);
  },wait:async()=>{}});
  assert.deepEqual(result,{ok:true});
  assert.equal(calls,2);
});
