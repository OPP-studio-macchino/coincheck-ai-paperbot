"use strict";
let token="", data=null, activeTab="decisions";
const el=id=>document.getElementById(id);
const text=(id,value)=>{el(id).textContent=String(value ?? "--")};
const yen=value=>value==null?"--":"¥"+Number(value).toLocaleString("ja-JP",{maximumFractionDigits:0});
const time=value=>value?new Date(value).toLocaleString("ja-JP",{timeZone:"Asia/Tokyo",hour12:false}):"--";
const short=value=>value==null?"--":Number(value).toFixed(8);
async function request(path,options={}){
 const response=await fetch(path,{cache:"no-store",...options,headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json",...(options.headers||{})}});
 if(!response.ok)throw Error(response.status===401?"管理画面トークンが違います。":"API error "+response.status);
 return response.json();
}
function item(textValue,tag="td"){
 const node=document.createElement(tag);node.textContent=textValue==null?"—":String(textValue);return node;
}
function makeWallet(w){
 const box=document.createElement("div");box.className="wallet";
 const title=document.createElement("div");title.className="name";title.textContent=w.name==="baseline"?"テクニカルのみ / BASELINE":"ニュース併用 / NEWS GATE";box.append(title);
 const amount=document.createElement("div");amount.className="amount";amount.textContent=yen(w.cash_jpy);box.append(amount);
 const dl=document.createElement("dl");
 const entries=[["保有BTC",short(w.btc_qty)],["平均購入価格",yen(w.entry_price)],["損切り価格",yen(w.stop_price)],["利確価格",yen(w.target_price)],["実現損益",yen(w.realized_jpy)]];
 for(const [k,v] of entries){dl.append(item(k,"dt"),item(v,"dd"));}box.append(dl);return box;
}
function tableRow(values){const tr=document.createElement("tr");for(const v of values)tr.append(item(v));return tr;}
function renderTable(rows){
 const head=el("tableHead"),body=el("tableBody");head.replaceChildren();body.replaceChildren();
 let columns=activeTab==="decisions"?["口座","判断日時 (JST)","条件","処理","理由"]:["口座","日時 (JST)","種別","BTC数量","約定価格","金額","理由"];
 const tr=document.createElement("tr");for(const x of columns)tr.append(item(x,"th"));head.append(tr);
 for(const row of rows){let vals=activeTab==="decisions"?[row.portfolio,time(row.bar_start),row.signal?"買い条件成立":"未成立",row.action,row.reason]:[row.portfolio,time(row.at),row.side,short(row.qty),yen(row.price),yen(row.total_jpy),row.reason];body.append(tableRow(vals));}
 if(rows.length===0)body.append(tableRow(["まだ該当履歴はありません"]));
}
async function refresh(){
 if(!token)return;
 try{
   const [state,decisions,fills,news]=await Promise.all([request("/api/status"),request("/api/decisions"),request("/api/fills"),request("/api/news")]);
   data={state,decisions,fills,news};
   text("authStatus","接続中 / "+time(state.server_time));
   text("ticks",Number(state.ticks).toLocaleString("ja-JP"));
   text("latest","最終取引："+time(state.last_trade_at));
   const sess=state.session;
   const connected=!!sess&&!sess.disconnected_at&&sess.last_trade_at&&Date.now()-Date.parse(sess.last_trade_at)<120000;
   text("connection",connected?"市場データ受信中":"通信未確認／切断／過去データ不足");
   text("pauseStatus",state.pause_new_buys?"停止中":"許可（仮想のみ）");
   text("pause",state.pause_new_buys?"新規仮想購入を再開":"新規仮想購入を一時停止");
   const container=el("wallets");container.replaceChildren();for(const x of state.portfolios)container.append(makeWallet(x));
   const report=state.news;
   const badge=el("newsBadge");badge.className="pill "+(report?.state||"pending");badge.textContent=(report?.state||"UNKNOWN").toUpperCase();
   text("newsSummary",report?.summary||"ニュース分析未取得。新規購入のニュース判定はUNKNOWNです。");
   text("newsTime","判定時刻："+time(report?.evaluated_at)+" ／ 有効期限："+time(report?.expires_at));
   const sourceList=el("newsSources");sourceList.replaceChildren();
   for(const source of (report?.sources||[])){
     try{const u=new URL(source.url);if(u.protocol!=="https:")continue;
       const a=document.createElement("a");a.href=u.href;a.target="_blank";a.rel="noreferrer noopener";a.textContent=source.title||u.hostname;sourceList.append(a);}catch{}
   }
   renderTable(activeTab==="decisions"?decisions:fills);
 }catch(err){text("authStatus",err.message);text("connection","データ取得失敗／要確認");}
}
el("connect").addEventListener("click",()=>{token=el("token").value.trim();el("token").value="";refresh()});
el("refresh").addEventListener("click",refresh);
for(const b of document.querySelectorAll(".tab"))b.addEventListener("click",()=>{activeTab=b.dataset.tab;for(const x of document.querySelectorAll(".tab"))x.classList.toggle("active",x===b);if(data)renderTable(activeTab==="decisions"?data.decisions:data.fills)});
el("pause").addEventListener("click",async()=>{if(!token||!data)return;const value=!data.state.pause_new_buys;const msg=value?"新しい仮想購入を停止します。保有BTCは売りません。":"新しい仮想購入を再開します。実注文は行いません。";if(!confirm(msg))return;try{await request("/api/control/pause",{method:"POST",body:JSON.stringify({paused:value})});await refresh()}catch(e){alert(e.message)}});
setInterval(()=>{text("clock",new Date().toLocaleString("ja-JP",{timeZone:"Asia/Tokyo"}));if(token)refresh()},30000);
text("clock",new Date().toLocaleString("ja-JP",{timeZone:"Asia/Tokyo"}));
