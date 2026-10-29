<template>
  <main class="game-page">
    <TerminalHeader title="近离 · 游戏服务监控" />
    <section class="game-toolbar">
      <div><h1>游戏服务</h1><p>游戏状态与主机资源分别监测 · 每 30 秒刷新</p></div>
      <button @click="refresh" :disabled="loading">{{ loading ? '读取中…' : '刷新数据' }}</button>
    </section>
    <p v-if="error" role="alert" class="error">{{ error }}；页面数据可能已过期。</p>
    <section class="summary"><span>已接入 <b>{{ games.length }}</b></span><span>正常 <b>{{ games.filter(g=>effectiveStatus(g)==='ok').length }}</b></span><span>异常或未知 <b>{{ games.filter(g=>effectiveStatus(g)!=='ok').length }}</b></span></section>
    <div class="game-grid">
      <article v-for="g in visibleGames" :key="g.id" class="game-card" :data-game="g.id">
        <div class="card-heading"><h2>{{ g.name }}</h2><span class="status" :class="effectiveStatus(g)">{{ statusLabel(g) }}</span></div>
        <a :href="'/#/server/'+g.server_id" class="host-link">{{ g.server_name || '关联服务器' }} ↗</a>
        <div class="online-count">{{ online(g) ?? '—' }}<small>{{ g.online_source }}</small></div>
        <div class="numbers"><div v-for="[key,label] in fields" :key="key"><span>{{ label }}</span><strong>{{ metric(g,key) }}</strong></div></div>
        <p class="sample-time">采样 {{ date(g.sampled_at) }}<span v-if="g.metrics.ts_full"> · 完整统计 {{ date(g.metrics.ts_full) }}</span></p>
        <button class="details-button" @click="toggle(g)">{{ selected===g.id ? '收起详情' : '状态详情与曲线' }}</button>
        <section v-if="selected===g.id" class="details">
          <label>在线趋势 <select v-model.number="hours" @change="loadHistory(g.id)"><option :value="1">1小时</option><option :value="6">6小时</option><option :value="24">24小时</option><option :value="168">7天</option></select></label>
          <p v-if="historyError" role="alert">{{ historyError }}</p>
          <svg v-if="history.length" class="chart" viewBox="0 0 500 115" role="img" :aria-label="g.name+'在线趋势'">
            <text x="2" y="12">{{ chartMax }}</text><line x1="0" y1="100" x2="500" y2="100" stroke="currentColor" opacity=".2"/>
            <polyline v-for="(points,i) in chartSegments" :key="i" :points="points" fill="none" stroke="currentColor" stroke-width="2"/>
          </svg>
          <p v-else>暂无历史采样</p><p class="sample-time">曲线缺口代表无有效采样，区间值取峰值。</p>
          <dl class="signals"><template v-for="[k,v] in signals(g)" :key="k"><dt>{{ labels[k] || k }}</dt><dd>{{ v }}</dd></template></dl>
        </section>
      </article>
    </div>
    <p v-if="route.query.server"><a href="/#/games">查看全部游戏</a></p>
    <p v-if="!visibleGames.length && !loading && !error">此范围暂无游戏服务</p>
    <footer>“—”表示未知；不同游戏的在线统计口径不同，不作玩家总数相加。</footer>
  </main>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import TerminalHeader from '../components/TerminalHeader.vue'
import { http } from '../utils/http'
const route=useRoute()
const games=ref([]),loading=ref(false),error=ref(''),selected=ref(''),history=ref([]),historyError=ref(''),hours=ref(6),clock=ref(Date.now()/1000)
const visibleGames=computed(()=>route.query.server ? games.value.filter(g=>g.server_id===route.query.server):games.value)
const fields=[['total','累计账号'],['roles','角色数'],['today','今日活跃'],['new_today','今日注册'],['peak_today','今日峰值'],['peak_all','历史峰值']]
const labels={db_up:'数据库',redis_up:'Redis',mongo_up:'MongoDB',proc:'游戏进程',platform:'平台服务',listen:'监听端口',health:'健康检查',tick_ok:'主循环心跳',deadlocked:'死锁线程',queue_failed:'失败队列',core_services:'核心服务',auth_up:'认证服务',bootstrap_up:'引导服务',wallet_up:'钱包服务',gm_proc:'GM进程',proc_s1001:'一区进程',proc_dist:'分发进程',proc_admin:'管理进程',proc_gateway:'网关进程',proc_game:'游戏进程',proc_battle:'战斗进程',proc_dgame:'游戏进程',proc_v6fwd:'IPv6转发',proc_cache:'缓存进程',bhc_gateway:'网关',bhc_gameserver:'游戏服',bhc_battleservice:'战斗服',bhc_crossserver:'跨服',bhc_gmserver:'GM服务'}
const expired=g=>!g.sampled_at||clock.value-g.sampled_at>180
const effectiveStatus=g=>expired(g)||g.metrics.collection_error?'unknown':g.status
const statusLabel=g=>expired(g)?'数据过期':g.metrics.collection_error?'采集失败':({ok:'正常',warn:'部分异常',down:'服务异常',unknown:'未知'}[g.status]||'未知')
const online=g=>effectiveStatus(g)==='unknown'?null:g.online
const metric=(g,k)=>expired(g)||g.metrics.collection_error||g.metrics[k]==null||g.metrics[k]<0?'—':g.metrics[k]
const date=t=>t?new Date(t*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'未采集'
const signals=g=>Object.entries(g.metrics).filter(([k,v])=>k in labels||/^(port_|bhc_port_|.*_port$)/.test(k)).map(([k,v])=>[k,expired(g)||g.metrics.collection_error?'未知':v===1?'正常':v===0?'未就绪':v])
const chartMax=computed(()=>Math.max(1,...history.value.map(p=>p.online||0)))
const chartSegments=computed(()=>{
 const segments=[];let current=[];const rows=history.value;const start=rows[0]?.ts||0,end=rows.at(-1)?.ts||start+1
 for(let i=0;i<rows.length;i++){const p=rows[i];if(p.online==null||p.issue||i&&p.ts-rows[i-1].ts>360){if(current.length)segments.push(current.join(' '));current=[];if(p.online==null||p.issue)continue}current.push(`${(p.ts-start)/Math.max(1,end-start)*500},${100-p.online/chartMax.value*80}`)}
 if(current.length)segments.push(current.join(' '));return segments
})
let timer,tick,historyRequest=0,disposed=false
async function loadHistory(id){const seq=++historyRequest;historyError.value='';const r=await http.get(`/api/games?id=${encodeURIComponent(id)}&hours=${hours.value}`,{timeoutMs:15000});if(seq!==historyRequest||selected.value!==id||disposed)return;if(r.error){historyError.value='曲线读取失败';history.value=[]}else history.value=r.data.history||[]}
async function toggle(g){history.value=[];historyError.value='';if(selected.value===g.id){selected.value='';historyRequest++;return}selected.value=g.id;await loadHistory(g.id)}
async function refresh(){if(loading.value)return;loading.value=true;try{const r=await http.get('/api/games',{timeoutMs:15000});if(disposed)return;if(r.error)error.value='读取游戏状态失败';else{games.value=r.data.games||[];error.value='';if(selected.value)await loadHistory(selected.value)}}finally{loading.value=false}}
onMounted(()=>{refresh();timer=setInterval(refresh,30000);tick=setInterval(()=>clock.value=Date.now()/1000,1000)})
onUnmounted(()=>{disposed=true;historyRequest++;clearInterval(timer);clearInterval(tick)})
</script>
<style scoped>
.game-page{max-width:1440px;margin:auto;padding:20px;color:var(--text-primary,#cbd5e1)}.game-toolbar{display:flex;justify-content:space-between;align-items:center;margin:30px 0 20px;gap:12px}.game-toolbar h1{font-size:26px;margin:0 0 8px}.game-toolbar p,.sample-time,footer{opacity:.65;font-size:12px}button,select{background:transparent;border:1px solid #64748b66;border-radius:8px;color:inherit;padding:9px 12px;cursor:pointer}.summary{display:flex;gap:30px;margin:24px 0}.summary b{margin-left:10px}.game-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:18px}.game-card{border:1px solid #64748b44;border-radius:14px;background:var(--bg-secondary,rgba(120,130,150,.05));padding:22px;align-self:start}.card-heading{display:flex;justify-content:space-between;gap:12px;align-items:center}.card-heading h2{font-size:19px;margin:0}.status{font-size:12px;border-radius:6px;padding:4px 8px;white-space:nowrap;background:#64748b33}.ok{color:#10b981}.warn{color:#f59e0b}.down{color:#f87171}.unknown{color:#94a3b8}.host-link{display:block;color:inherit;opacity:.65;font-size:12px;margin:10px 0;text-decoration:none}.online-count{font-size:40px;font-weight:600;margin:22px 0}.online-count small{display:block;font-size:12px;font-weight:400;opacity:.65;margin-top:4px}.numbers{display:grid;grid-template-columns:repeat(3,1fr);gap:20px 8px}.numbers span{display:block;font-size:12px;opacity:.65;margin-bottom:6px}.numbers strong{font-size:19px}.sample-time{line-height:1.7}.details-button{width:100%;margin-top:8px}.details{padding-top:18px}.chart{width:100%;margin-top:16px;color:#38bdf8}.chart text{font-size:10px;fill:currentColor}.signals{display:grid;grid-template-columns:1fr auto;font-size:12px;gap:10px}.signals dt{overflow-wrap:anywhere}.signals dd{margin:0}footer{margin:30px 0}.error{color:#fb7185}option{color:#111;background:white}@media(max-width:600px){.game-page{padding:12px}.game-grid{grid-template-columns:1fr}.summary{gap:12px;font-size:13px}.game-toolbar p{max-width:200px}}
</style>
