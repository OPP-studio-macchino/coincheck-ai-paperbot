"use strict";
let bearer="",data=null,activeTab="decisions";
const el=id=>document.getElementById(id);
const text=(id,value)=>{const target=el(id);if(target)target.textContent=String(value??"—")};
const yen=v=>v==null?"評価不能":"¥"+Number(v).toLocaleString("ja-JP",{maximumFractionDigits:0});
const time=v=>v?new Date(v).toLocaleString("ja-JP",{timeZone:"Asia/Tokyo",hour12:false}):"—";
const num=v=>v==null?"—":Number(v).toLocaleString("ja-JP");
const eight=v=>v==null?"—":Number(v).toFixed(8);
function item(value,tag="td"){const n=document.createElement(tag);n.textContent=value==null?"—":String(value);return n;}
async function request(path,options={}){
 const response=await fetch(path,{...options,cache:"no-store",mode:"same-origin",
  headers:{"Authorization":"Bearer "+bearer,"Accept":"application/json",
  ...(options.method==="POST"?{"Content-Type":"application/json"}:{}),...(options.headers||{})}});
 if(!response.ok)throw Error(response.status===401?"認証に失敗しました。":
  response.status===403?"閲覧トークンには操作権限がありません。":
  "APIエラー: "+response.status);
 return response.json();
}
function makeWallet(w){
 const box=document.createElement("div");box.className="wallet";
 const title=document.createElement("div");title.className="name";
 title.textContent=w.name==="baseline"?"テクニカルのみ / BASELINE":"ニュース併用 / NEWS GATE";
 box.append(title);
 const cash=document.createElement("div");cash.className="amount";cash.textContent=yen(w.cash_jpy);box.append(cash);
 const dl=document.createElement("dl");
 const details=[
   ["仮想JPY現金",yen(w.cash_jpy)],["仮想BTC数量",eight(w.btc_qty)],
   ["平均購入単価",yen(w.entry_price)],["固定損切り価格",yen(w.stop_price)],
   ["固定利確価格",yen(w.target_price)],["実現損益",yen(w.realized_jpy)],
   ["評価額",w.valuation_jpy==null?"価格未確認につき評価不能":yen(w.valuation_jpy)],
   ["含み損益",w.unrealized_pnl_jpy==null?"価格未確認につき評価不能":yen(w.unrealized_pnl_jpy)],
 ];
 for(const [a,b] of details)dl.append(item(a,"dt"),item(b,"dd"));
 box.append(dl);return box;
}
function tabRows(){
 if(!data)return;
 const head=el("tableHead"),body=el("tableBody");head.replaceChildren();body.replaceChildren();
 const sets={
  decisions:["口座","判断足 (JST)","シグナル","動作","理由"],
  fills:["口座","時刻 (JST)","区分","BTC数量","約定単価","仮想JPY金額","理由"],
  audit:["時刻 (JST)","主体","リクエストID","操作","変更前","変更後","結果","理由"],
 };
 const hdr=document.createElement("tr");
 for(const name of sets[activeTab])hdr.append(item(name,"th"));
 head.append(hdr);
 const rows=data[activeTab]||[];
 for(const row of rows){
  let cells;
  if(activeTab==="decisions")cells=[row.portfolio,time(row.bar_start),
       row.signal?"買い候補":"未成立",row.action,row.reason];
  else if(activeTab==="fills")cells=[row.portfolio,time(row.at),row.side,
       eight(row.qty),yen(row.price),yen(row.total_jpy),row.reason];
  else cells=[time(row.at),row.actor_id,row.request_id,row.action,
       JSON.stringify(row.previous),JSON.stringify(row.next_state),row.result,row.reason];
  const tr=document.createElement("tr");for(const c of cells)tr.append(item(c));body.append(tr);
 }
 if(rows.length===0){const tr=document.createElement("tr");tr.append(item("まだ履歴はありません"));body.append(tr);}
}
function showDetails(state){
 const runtime=state.runtime||{},hist=state.history||{};
 const dl=el("runDetails");dl.replaceChildren();
 const rows=[
  ["Git commit SHA",runtime.git_sha||"市場プロセス未報告"],
  ["戦略版",runtime.strategy_version||"未報告"],
  ["設定版",runtime.config_version||"未報告"],
  ["DRY_RUN",state.mode==="DRY_RUN_ONLY"?"true":"確認不能"],
  ["ニュースモード",runtime.news_mode??"未報告"],
  ["購入予算 (税込手数料込み上限)",state.effective_buy_budget_jpy==null?
      "未報告":yen(state.effective_buy_budget_jpy)],
  ["初期仮想資金の設定",runtime.initial_config_jpy==null?"未報告":yen(runtime.initial_config_jpy)],
  ["仮想手数料 (bps)",runtime.fee_bps??"未報告"],
  ["Coincheck実資産接続",state.real_coincheck_assets==="unconnected"?"未接続":"確認不能"],
  ["市場最終受信",time(state.last_trade_at)],
  ["2時間足の確定連続本数",num(hist["2h_confirmed_contiguous"])+"/"+num(hist["2h_required"])],
  ["4時間足の確定連続本数",num(hist["4h_confirmed_contiguous"])+"/"+num(hist["4h_required"])],
  ["判定準備",hist.history_ready?"有効":"未完了・保留"],
  ["最終決済評価",time(state.exit_evaluation?.last_evaluated_at)]
 ];
 for(const [name,value] of rows)dl.append(item(name,"dt"),item(value,"dd"));
 text("historyReason","不足・停止理由："+(hist.reason||"未確認"));
 const x=state.exit_evaluation;
 text("exitReason","決済評価："+(x?.available?"実行可能":"評価不能／要確認")+
      " ／ "+(x?.reason||"unknown")+" ／ 最終："+time(x?.last_evaluated_at));
 text("valuationNote","評価方式："+(state.valuation_method||"不明")+
      "。使用価格："+(state.market_price_fresh?yen(state.last_trade_price):"古い／未取得のため使用不可")+
      " ／ 価格時刻："+time(state.last_trade_at));
}
async function refresh(){
 if(!bearer)return;
 try{
   const [state,decisions,fills,news,audit]=await Promise.all([
       request("/api/status"),request("/api/decisions"),request("/api/fills"),
       request("/api/news"),request("/api/audit")]);
   data={state,decisions,fills,news,audit};
   text("authStatus",state.actor_role==="admin"?"操作管理者 / "+time(state.server_time):
       "閲覧専用 / "+time(state.server_time));
   text("ticks",num(state.ticks));
   const connected=state.history?.market_fresh===true;
   text("connection",connected?"受信中":"通信未確認");
   text("latest","最新受信："+time(state.last_trade_at));
   text("buyLimit",state.effective_buy_budget_jpy==null?"未報告":yen(state.effective_buy_budget_jpy));
   text("pauseStatus",state.pause_new_buys?"停止中":"許可（仮想のみ）");
   const p=el("pause");p.disabled=state.actor_role!=="admin";
   p.textContent=p.disabled?"閲覧専用・操作不可":
       state.pause_new_buys?"新規仮想購入を再開":"新規仮想購入を一時停止";
   showDetails(state);
   const wallets=el("wallets");wallets.replaceChildren();
   for(const w of state.portfolios)wallets.append(makeWallet(w));
   const n=state.news;const badge=el("newsBadge");
   badge.className="pill "+(n?.state||"pending");
   badge.textContent=(n?.state||"UNKNOWN").toUpperCase();
   text("newsSummary",n?.summary||"ニュース未確認。news_gateは購入しません。");
   text("newsTime","判定："+time(n?.evaluated_at)+" ／ 有効期限："+time(n?.expires_at));
   const sources=el("newsSources");sources.replaceChildren();
   for(const s of n?.sources||[]){
     try{const u=new URL(s.url);if(u.protocol!=="https:"||u.username||u.password)continue;
       const a=document.createElement("a");a.href=u.href;a.target="_blank";
       a.rel="noopener noreferrer";a.textContent=s.title||u.hostname;sources.append(a);}catch{}
   }
   tabRows();
 }catch(e){text("authStatus",e.message);text("connection","取得失敗／停止");el("pause").disabled=true;}
}
el("connect").addEventListener("click",()=>{
 bearer=el("token").value.trim();el("token").value="";refresh();
});
el("refresh").addEventListener("click",refresh);
for(const b of document.querySelectorAll(".tab")){
 b.addEventListener("click",()=>{
  activeTab=b.dataset.tab;
  for(const x of document.querySelectorAll(".tab"))x.classList.toggle("active",x===b);
  tabRows();
 });
}
el("pause").addEventListener("click",async()=>{
 if(!bearer||!data||data.state.actor_role!=="admin")return;
 const next=!data.state.pause_new_buys;
 const message=next?"新規の仮想購入だけを停止します。保有分のATR決済は継続します。":
       "新規仮想購入を再開します。ただしデータ品質・ニュース安全条件は解除しません。";
 if(!confirm(message))return;
 try{
  const result=await request("/api/control/pause",
       {method:"POST",body:JSON.stringify({paused:next,reason:"admin_ui"})});
  text("operationResult","処理完了・監査ID："+result.request_id);
  await refresh();
 }catch(e){text("operationResult",e.message);}
});
setInterval(()=>{
 text("clock",new Date().toLocaleString("ja-JP",{timeZone:"Asia/Tokyo"}));
 if(bearer)refresh();
},30000);
text("clock",new Date().toLocaleString("ja-JP",{timeZone:"Asia/Tokyo"}));
