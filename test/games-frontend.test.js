import test from 'node:test';
import assert from 'node:assert/strict';
import {serveFrontend} from '../src/handlers/frontend.js';
test('dedicated game entry always loads built-in router when remote theme is configured',async()=>{
 let fetched=0;
 const env={ASSETS:{async fetch(){fetched++;return new Response('<html><head><title>test</title></head><body>builtin-game-router</body></html>')}}};
 const settings={theme_url:'https://github.com/example/theme/tree/main'};
 const response=await serveFrontend(new Request('https://monitor.test/games'),env,settings);
 assert.equal(response.status,200);
 assert.match(await response.text(),/builtin-game-router/);
 assert.equal(fetched,1);
});
