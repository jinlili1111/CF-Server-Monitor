import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import { verifyReport, validateSample, presentGame, handleGameReport } from '../src/games/service.js';
const secret='x'.repeat(64),now=Math.floor(Date.now()/1000);
const sign=async (body,timestamp)=>{
 const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 return Buffer.from(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(timestamp+'\n'+body))).toString('hex');
};
const request=async(data,timestamp=String(now),signature=null)=>{const body=JSON.stringify(data);return new Request('https://monitor.test/games/update',{method:'POST',body,headers:{'X-Game-Timestamp':timestamp,'X-Game-Signature':signature||await sign(body,timestamp)}})};
function database(){
 const sql=new DatabaseSync(':memory:');sql.exec(fs.readFileSync(new URL('../migrations/0001_games.sql',import.meta.url),'utf8'));
 sql.exec("INSERT INTO game_services(id,name,server_id,online_source) VALUES('game','Game','host','sessions')");
 const prepare=query=>{const stmt=sql.prepare(query);let values=[];return{bind(...v){values=v;return this},async all(){return{results:stmt.all(...values)}},async run(){return stmt.run(...values)}}};
 return{sql,prepare,async batch(statements){sql.exec('BEGIN');try{const result=[];for(const s of statements)result.push(await s.run());sql.exec('COMMIT');return result}catch(e){sql.exec('ROLLBACK');throw e}}};
}
const sample=(ts=now,online=4)=>({id:'game',sampled_at:ts,metrics:{online,status:'ok'}});
test('signed reports bind exact body and timestamp; expired or altered reports rejected',async()=>{
 const sig=await sign('{}',String(now));assert.equal(await verifyReport(secret,String(now),sig,'{}',now),true);
 assert.equal(await verifyReport(secret,String(now),sig,'{"x":1}',now),false);
 assert.equal(await verifyReport(secret,String(now),sig,'{}',now+121),false);
 assert.equal(await verifyReport('',String(now),sig,'{}',now),false);
});
test('unknown stays unknown and error overrides stale successful metrics',()=>{
 const s=validateSample({id:'game',sampled_at:now,metrics:{online:40,status:'ok',collection_error:true,password:'hidden'}},now);
 assert.equal(s.metrics.online,-1);assert.equal(s.metrics.status,'unknown');assert.equal(s.metrics.password,undefined);
 const row={id:'game',sampled_at:now-181,metrics_json:JSON.stringify({online:4,status:'ok'})};
 assert.equal(presentGame(row,now).online,null);assert.equal(presentGame(row,now).status,'unknown');
 assert.throws(()=>validateSample(sample(now+121),now));assert.throws(()=>validateSample(sample(now,1.2),now));
});
test('real SQLite batch stores latest/history atomically, deduplicates retries, ignores older data',async()=>{
 const db=database(),env={DB:db,GAME_REPORT_SECRET:secret};
 assert.equal((await handleGameReport(await request({games:[sample()]}),env)).status,200);
 assert.equal((await handleGameReport(await request({games:[sample(now,999)]}),env)).status,200);
 assert.equal((await handleGameReport(await request({games:[sample(now-30,88)]}),env)).status,200);
 assert.equal(JSON.parse(db.sql.prepare('SELECT metrics_json FROM game_services').get().metrics_json).online,4);
 assert.equal(db.sql.prepare('SELECT COUNT(*) AS n FROM game_history').get().n,1);
 assert.equal((await handleGameReport(await request({games:[{...sample(),id:'other'}]}),env)).status,403);
 assert.equal((await handleGameReport(await request({games:[sample(),sample()]}),env)).status,400);
});
test('unauthorized/oversized payloads cannot update state',async()=>{
 const db=database(),env={DB:db,GAME_REPORT_SECRET:secret};
 assert.equal((await handleGameReport(await request({games:[sample()]},String(now),'0'.repeat(64)),env)).status,401);
 const req=new Request('https://monitor.test/games/update',{method:'POST',body:'x'.repeat(32769)});
 assert.equal((await handleGameReport(req,env)).status,413);
 assert.equal(db.sql.prepare('SELECT sampled_at FROM game_services').get().sampled_at,0);
});
