"""A script-free local research dashboard, generated from the actual ledger."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .contracts import LabError

LABELS = {
    "INSUFFICIENT_CLOSED_TRADES": "決済件数が不足",
    "NET_NONPOSITIVE": "費用込み利益が0円以下",
    "NO_BASELINE_IMPROVEMENT": "固定戦略を上回らない",
    "DRAWDOWN_LIMIT": "最大下落率が上限超過",
    "PROFIT_CONCENTRATION": "利益が少数の取引に集中",
    "COST_STRESS_FAILED": "費用2倍の基準に未達",
    "DEV_FOLD_STABILITY": "期間を分けた試験で安定性不足",
    "NO_IMPROVEMENT": "採用候補なし",
    "REVIEW_REQUIRED": "人による審査待ち",
    "DEMO_ONLY": "合成データでの機能検証完了",
    "HOLDOUT_FAILED": "最終評価の基準に未達",
    "HOLDOUT_ALREADY_CONSUMED": "最終評価期間は使用済み",
    "TIME_LIMIT": "実行時間の上限に到達",
    "REJECTED": "不採用",
    "SHORTLISTED": "開発基準を通過",
    "FAILED": "試行失敗",
    "DUPLICATE": "重複候補",
}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def write_report(snapshot: dict, destination: Path) -> None:
    if destination.is_symlink() or destination.suffix != ".html":
        raise LabError("REPORT_PATH_INVALID")
    run = snapshot["run"]
    manifest = run["manifest"]
    result = run.get("result") or {}
    rows = []
    for trial in snapshot["trials"]:
        candidate = trial.get("candidate") or {}
        evaluation = trial.get("result") or {}
        dev = evaluation.get("development", {})
        net = dev.get("net_profit_jpy")
        net_text = f"¥{net:,.2f}" if type(net) in (int, float) else "—"
        dd = dev.get("max_drawdown_pct")
        dd_text = f"{dd:.2f}%" if type(dd) in (int, float) else "—"
        codes = [trial["error_code"]] if trial.get("error_code") else evaluation.get("gate_reasons", [])
        reason = " / ".join(LABELS.get(code, code) for code in codes) or "—"
        rows.append(f"""<tr><td>{trial['trial_index']+1:02d}</td>
        <td><strong>{esc(candidate.get('name','候補生成失敗'))}</strong><small>{esc(trial.get('candidate_hash','') or '')[:12]}</small></td>
        <td>{esc(candidate.get('timeframe_minutes','—'))}分<br><small>EMA {esc(candidate.get('ema_fast','—'))}/{esc(candidate.get('ema_slow','—'))} · RSI {esc(candidate.get('rsi_period','—'))}</small></td>
        <td>{esc(candidate.get('stop_atr','—'))} / {esc(candidate.get('target_atr','—'))}</td>
        <td>{net_text}<small>DD {dd_text} · 通常決済 {esc(dev.get('closed_trades','—'))}件</small></td>
        <td><span class="pill">{esc(LABELS.get(trial['status'],trial['status']))}</span><small>{esc(reason)}</small></td></tr>""")
    provenance = manifest.get("provenance", {})
    is_demo = provenance.get("source") == "synthetic"
    banner = "合成価格による機能検証。実相場の運用成績ではありません。" if is_demo else "利用者提供の過去データによる研究。取引所・銘柄・収集時刻の真正性、実運用の利益は未検証です。"
    generation_mode = manifest.get("generation_mode")
    if generation_mode == "openai_responses":
        generation_text = ("生成方式：OpenAI応答の保存候補。評価処理からのAPI呼出しは0回。"
                           "生成元・usage・費用は保存記録の申告値で、成果物hashは署名ではありません。")
    elif generation_mode == "fixture" or manifest.get("provider_mode") == "offline_fixture":
        generation_text = ("生成方式：固定応答（OpenAIへの送信・課金なし）。"
                           "生成記録にusage・費用がある場合は模擬値です。")
    else:
        generation_text = "生成方式：この記録では未確認。評価処理に外部APIを呼び出す機能はありません。"
    outcome = result.get("outcome", "RUNNING")
    buy = manifest.get("buy_budget_jpy", 0)
    initial = manifest.get("initial_cash_jpy")
    initial_text = f"¥{initial:,.0f}" if type(initial) in (int, float) else "—"
    baseline = result.get("baseline_development", {})
    base_profit = baseline.get("net_profit_jpy")
    base_text = f"¥{base_profit:,.2f}" if type(base_profit) in (int, float) else "—"
    fallback = "研究候補の比較が完了しました。" if run.get("status") == "COMPLETED" else "実験は未完了です。保存済みの進捗を表示しています。"
    gate_text = " / ".join(LABELS.get(x,x) for x in result.get("reasons", [])) or fallback
    finals = snapshot.get("final_tests", [])
    if any(x["status"] == "RESERVED" for x in finals):
        final_status = "予約済み・結果未確定。この区間は消費済みで、再利用できません。"
    elif finals or result.get("final_test"):
        final_status = "実施済み（この区間は再利用不可）"
    elif outcome == "HOLDOUT_ALREADY_CONSUMED":
        final_status = "別の実験等で消費済み。この区間を再利用できません。"
    else:
        final_status = "未実施：開発区間の採用基準を通過した候補のみ確認"
    details = {"manifest": manifest, "run_status":run.get("status"), "result": result, "final_tests":finals,
               "event_chain_ok": snapshot["event_chain_ok"]}
    document = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>AI研究室 · {esc(run['run_id'])}</title>
<style>
:root{{color-scheme:light;--ink:#152333;--muted:#637386;--line:#dbe3e9;--bg:#f3f6f8;--teal:#006b62}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,-apple-system,'Noto Sans JP',sans-serif}}
main{{max-width:1280px;margin:auto;padding:38px 32px 64px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}
.eyebrow{{color:var(--teal);font-weight:750;font-size:12px;letter-spacing:.13em}}h1{{font-size:34px;line-height:1.3;letter-spacing:-.03em;margin:7px 0 8px}}h2{{font-size:19px;margin:0 0 12px}}
p{{margin:6px 0}}.muted,small{{color:var(--muted)}}small{{display:block;font-size:12px;margin-top:4px;overflow-wrap:anywhere}}
.badge{{background:#e0f0eb;color:#155f4c;border:1px solid #b7ddcc;border-radius:25px;padding:7px 13px;white-space:nowrap;font-weight:650;font-size:12px}}
.notice{{background:#fff7e1;border-left:4px solid #be8423;padding:15px 18px;margin:24px 0;color:#785515}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-bottom:22px}}.card{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:21px}}
.value{{font-size:27px;font-weight:720;margin:8px 0 4px;line-height:1.25}}.label{{font-size:12px;color:var(--muted)}}
.summary{{display:grid;grid-template-columns:1.3fr 1fr;gap:20px;margin-bottom:22px}}.outcome{{font-size:21px;font-weight:700;color:var(--teal)}}
.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th{{font-weight:650;color:var(--muted);text-align:left;background:#f6f8fa}}
td,th{{padding:14px 12px;border-bottom:1px solid var(--line);vertical-align:top}}td:first-child{{color:var(--muted)}}.pill{{font-size:11px;display:inline-block;padding:3px 8px;border-radius:5px;background:#edf1f5;font-weight:700}}
details{{margin-top:22px}}summary{{cursor:pointer;font-weight:650}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#162536;color:#dce5ef;padding:20px;border-radius:10px;font-size:11px;max-height:520px;overflow:auto}}
footer{{margin-top:24px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px}}code{{font-size:12px}}
@media(max-width:800px){{main{{padding:24px 16px}}.metrics{{grid-template-columns:repeat(2,1fr)}}.summary{{grid-template-columns:1fr}}header{{flex-direction:column}}h1{{font-size:27px}}}}
</style></head><body><main>
<header><div><div class="eyebrow">O.P.P STUDIO / COINCHECK BOT</div><h1>AI自己改善エンジン · 研究室</h1>
<p class="muted">候補を作り、同じ条件で比較し、採否の根拠を残す。</p></div><span class="badge">研究専用 · 実口座未接続</span></header>
<div class="notice">{banner}　{esc(generation_text)}</div>
<div class="metrics"><section class="card"><div class="label">候補の試行数 / 上限</div><div class="value">{len(snapshot['trials'])} / {esc(manifest.get('max_trials','—'))}</div><small>失敗・不正・重複も消費に含めます</small></section>
<section class="card"><div class="label">1回の仮想購入上限</div><div class="value">¥{buy:,.0f}</div><small>手数料込み · 初期仮想資金 {initial_text}</small></section>
<section class="card"><div class="label">固定2時間足の開発区間損益</div><div class="value">{base_text}</div><small>費用仮定込み・評価用の区間末決済を含む</small></section>
<section class="card"><div class="label">稼働Botへの自動反映</div><div class="value">無効</div><small>このMVPにはリリース・発注機能がありません</small></section></div>
<div class="summary"><section class="card"><h2>今回の判定</h2><div class="outcome">{esc(LABELS.get(outcome,outcome))}</div><p>{esc(gate_text)}</p><small>候補が基準を満たしても、既存戦略・既存ポジションは変更しません。</small></section>
<section class="card"><h2>未使用区間での最終確認</h2><p>{esc(final_status)}</p><small>最終確認の成績は、次の候補生成へのフィードバックに渡しません。</small></section></div>
<section class="card"><h2>候補と検証結果</h2><p class="muted">同じデータ分割・資金・費用・決済規則で比較します。ATR欄は損切り / 利確倍率です。</p>
<div class="table-wrap"><table><thead><tr><th>#</th><th>候補</th><th>時間足・指標</th><th>ATR倍率</th><th>開発区間</th><th>状態・理由</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<details class="card"><summary>再現用の設定・データ識別子・判定根拠</summary><pre>{esc(json.dumps(details,ensure_ascii=False,indent=2))}</pre></details>
<footer>実行ID：<code>{esc(run['run_id'])}</code>　記録時刻（UTC）：{esc(run['started_at'])}<br>
時刻はすべてUTC。表示は保存時点の研究結果で、現在の相場・資産評価ではありません。監査連鎖：{'整合' if snapshot['event_chain_ok'] else '不整合'}。
ローカルの管理者による台帳全体の改変まで防ぐ暗号署名ではありません。</footer>
</main></body></html>"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".html.partial")
    if temporary.is_symlink():
        raise LabError("REPORT_PATH_INVALID")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(document)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
