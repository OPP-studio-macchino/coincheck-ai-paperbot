# Phase 2 実接続準備・再開記録 — 2026-11-06

## 2026-11-06 18:37 JST 追記：初回live生成試行の失敗と停止

**実AIによる候補生成は未成功です。** 18:26 JSTにキーの形式とファイル権限（秘密ファイル0600、直接親0700）を値を表示せず確認しました。固定要求と費用台帳の事前チェックは成功し、18:28 JSTにモデル gpt-4.1-2025-04-14 で**生成を1回だけ**実行しました。コードは固定のPHASE2_COMMAND_FAILEDで終了し、モデル応答ID・候補hash・保存artifactはありません。

| live台帳 | 実測値 |
|---|---|
| 対象コード | 9d958cc7ab195805b2ae479545d2d5cde07a8286、取り込み済みPR #5 |
| live試行 | **1回（FAILED）** |
| 予約額 | **2,111,536 microUSD**、失敗後も保持 |
| usageから判明した費用 | **0（確認値なし）**。**課金ゼロと断定しない** |
| unknown-cost試行 | **1回** |
| 再試行・追加の有料生成 | **0回** |
| 監査chain | PASS |
| OpenAIのモデル参照 | 18:33 JST、GET /v1/models/gpt-4.1-2025-04-14 で**HTTP 200**（認証・モデル参照可）。これは候補生成や課金可否の証明ではない |

現行の生成器は安全確保のためHTTPエラー応答やパーサー失敗の詳細を抑止しており、**初回失敗がHTTP 400/401/429か、応答パース・usage検査の失敗かは断定できません**。診断修正ではHTTPステータス番号・処理段階だけをallowlistから表示し、秘密・HTTP応答本文・headers・例外文字列は表示しません。

**停止条件：既存live台帳の削除、初期化、複製、同じ要求の自動再POST、新しい台帳を使う無断の有料再試行を禁止。** 新しい有料試行は、キーの課金状態・診断コード・新しいsource identityに結び付けた入力を確認し、所有者が改めて1回分の費用上限を許可した場合のみ行います。今回の失敗に起因する不明費用も考慮します。Bot本体・DB・VPS・実注文には変更なしです。

以下の第1〜6章は**13:04 JST以前の準備時点のスナップショット**です。キー未配置、live試行0回、初回生成手順などの記述は、その時点の履歴として残しており、上記の新しい状態を上書きしません。

## 1. 現在の到達点（初回試行前の記録）

**実OpenAI APIへの接続と候補生成は未完了です。** 専用キーは未配置、live台帳の試行数は0です。実モデルの候補取得・保存・隔離評価までを1回の有限な試験として完了させます。稼働Bot、既存DB・残高・volume、VPSには反映していません。

| 項目 | 確認済みの状態 |
|---|---|
| 対象コードcommit | `3f0b29576a6685be650cdafa2e2fd928298100be` |
| 改修PR | [PR #5](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/pull/5) |
| 改修内容 | prompt v2へ、候補の制約をschema/policyから生成して明示 |
| 新worktree | `/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-phase2-live-20261106` |
| 維持する設定 | `DRY_RUN=true`、`LIVE_TRADING_ENABLED=false`、`NEWS_MODE=mock` |
| 試験の購入予算 | 手数料込み50,000円。研究の初期総資金とは別の設定 |

旧Botの戦略・設定は変更していません。実注文、Coincheck private API、既存ニュース用キーの流用はこの試験に含みません。

## 2. 凍結した入力と費用台帳

worktreeの `runtime/ai-phase2-input/` に、`synthetic-180d-seed47.csv`、`request.json`、`limits.json` を配置済みです。CSVは5分足51,840本・180日分の**合成データ**で、実市場での利益や改善を示す根拠にはなりません。

| 識別対象 | SHA-256 |
|---|---|
| 入力CSV | `6b0fc806fbf8b0698f1e0200760af6eb7bd8afb079fcf93112d0e624d4ac645c` |
| API要求 | `594b8d88dc8eaef50153880a4a51457c8bc9c7b42dc76afedddb532ca3437895` |
| 評価コード・policy等のsource hash | `4af2918f6ec4ec0b66fb3ed01b043a708b3b7affbe68208d03637eb78a9f6dfe` |

台帳は `runtime/ai_phase2_state/` に保存し、fixtureとliveを分離しています。

| 台帳・制限 | 現在値 |
|---|---|
| fixture試行数 | 1。模擬応答であり実APIの証跡ではない |
| live試行数 | 0 |
| 1回・日次・累積の費用上限 | 各2,111,536 microUSD（2.111536 USD） |
| 日次・累積の回数上限 | 各1回 |

予約額は固定モデルの最大入力枠と最大出力を保守的に計上した値で、実請求額ではありません。成功・失敗・タイムアウトとも呼出し前の予約を消費し、自動retryはしません。台帳の削除、再初期化、複製による枠の追加は行いません。

## 3. 検証結果と未確認項目

| 確認項目 | 結果 |
|---|---|
| ローカルunit tests | 152件PASS、15.097秒 |
| iMac unit tests | 152件PASS、11.561秒 |
| 到達可能なGit履歴の秘密検査 | 252件PASS。秘密値を表示しない検査 |
| `api.openai.com:443`へのTLS接続 | TLS 1.3、証明書検証ありで成功 |
| 上記TLS確認でのHTTP送信 | 0件。認証・Responses API成功の検証ではない |
| `secrets/openai_phase2_key` | 未配置 |
| 対象コードcommitのrequired CI | [security SUCCESS](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/actions/runs/42159852445)。対象は `3f0b295` |
| evaluator image | build成功。revisionは上記commit、UID10001:10001 |
| native生成要求とDocker評価要求 | 同じCSV・予算から再構成した要求書が完全一致 |
| evaluatorコンテナの実隔離 | inspectによる設定検査PASS。実ネットワークinterfaceはloopbackのみ |
| fixture成果物の実コンテナ評価 | 1試行・COMPLETED・NO_IMPROVEMENT、終了コード0、監査chain正常 |
| 実OpenAI応答・候補保存・実候補の評価 | **未確認：live試行は未実施** |

評価イメージIDは `sha256:de54831ee42e262869df0602cee6d2c639a2d3c0d7e1cd8562a96722a6d069e0` です。固定応答の結果は実AIの結果として数えません。終了時にもlive台帳は試行0、予約額0、既知費用0、監査chain正常であることを確認しました。

## 4. 専用Docker環境

既存profileとは別に、外付けドライブ上の `/Volumes/OPP_ARCHIVE/O.P.P Studio/cc-ai2-20261106` を専用基点としています。

| 項目 | 設定 |
|---|---|
| Colima保存先 | 基点下の `c`（`COLIMA_HOME`） |
| cache | 基点下の `cache` |
| Docker設定 | 基点下の `d`（`DOCKER_CONFIG`） |
| profile / context | `phase2` / `colima-phase2` |
| VM資源 | 2 CPU、2 GiBメモリ、data 10 GiB、root 10 GiB |
| ホスト入力mount | 検証用input stageのread-only mountのみ |
| context自動切替 | `--activate=false` |
| 既存profile | 停止状態を維持 |

評価器は専用contextで作成し、UID10001、`network=none`、read-only rootfs、`cap_drop=ALL`、`no-new-privileges`、PID32、メモリ512 MiB、CPU 0.5、公開portなしを実際のcontainer inspectで確認済みです。入力はread-only、書込み領域は専用の新規volume `cc-ai2-fixture-evaluation-20261106` だけです。既存volumeは対象にしていません。

`runtime/docker-preflight-evidence.json` と `runtime/fixture-preflight-20261106.json` / `.html` に証跡を保存しました。生成台帳・キーを評価器へ渡していません。試験後は専用VMを停止し、イメージ・volume・費用台帳は保持します。

## 5. 実接続を再開する手順（初回試行前の手順、再実行禁止）

> **初回live試行は既に消費済みです。** 以下のgenerateコマンドを同じ台帳で再実行しても成功せず、別台帳の新規生成には所有者による新しい有料試行許可が必要です。

1. PRのCI結果、実行commit、入力・要求・source hashを照合します。生成器と評価器で同じ要求書を再構成できることを無料で検証します。コードを変更した場合は、有料呼出し前に要求書との不一致を解消します。
2. 研究専用キーを安全な設定経路で配置します。チャットへの貼付は不要です。配置先はこのworktreeの `secrets/openai_phase2_key`、ファイル0400/0600、直接親0700、所有者は実行ユーザーです。値はログ・引数・Gitへ出しません。
3. `python3 -B -m ai_lab.phase2 status --state runtime/ai_phase2_state --mode live` で、既存台帳と残り枠を確認します。ここで台帳を初期化し直しません。
4. このworktreeで、最小環境のnative generatorを**1回だけ**実行します。キーのファイルパスはこのコマンドだけへ渡します。

```bash
LAB_ROOT='/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-phase2-live-20261106'
cd "$LAB_ROOT"
env -i PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
  DRY_RUN=true LIVE_TRADING_ENABLED=false NEWS_MODE=mock \
  AI_LAB_PHASE2_LIVE=true \
  OPENAI_API_KEY_FILE="$LAB_ROOT/secrets/openai_phase2_key" \
  python3 -B -m ai_lab.phase2 generate \
    --state "$LAB_ROOT/runtime/ai_phase2_state" \
    --context "$LAB_ROOT/runtime/ai-phase2-input/request.json" \
    --mode live
```

5. 成功時の `artifact_path` と台帳を照合します。凍結CSVと検証済みartifactの**非秘密コピーだけ**をread-only入力として、network noneの評価器へ渡します。キー、生成器の台帳、Bot DBは渡しません。購入予算は同じ50,000円、評価試行数は1です。
6. レポート、候補hash、応答識別子、model、usage、費用換算、隔離設定、終了コードを保存して、この記録の未確認欄を実結果で更新します。失敗・中断・結果不明なら証跡と台帳を保持して調査し、retryやstate再初期化で解消しません。

評価器の再開には、既存Botのcontextではなく上記専用の `COLIMA_HOME` / `COLIMA_CACHE_HOME` / `DOCKER_CONFIG` を指定し、`colima start phase2 --activate=false` を使います。実AI候補の評価はfixtureとは別名の新規評価volumeに保存し、入力は固定CSVと検証済み候補だけに限定します。これは合成fixtureと実APIの接続検収を分けるための領域であり、実市場holdoutや費用上限を再利用する回避策ではありません。

## 6. 完了の判定と反映範囲

実接続完了は、実応答に基づく `generation_mode=openai_responses` の候補が保存され、同じ入力・ソース・予算に結び付いた隔離評価まで終わった時点です。評価器自身の `actual_openai_calls=0` と、上流生成器の実呼出し数は区別します。`NO_IMPROVEMENT`や候補不採用も正常な研究結果です。合成データ上の結果から収益性を保証しません。

この作業で稼働Botへの自動反映は行いません。独立レビュアーのApproveを必須にせず、ユーザーが選択した開発方針を維持します。required CI等の既存保護は維持し、実行結果の保存とコード差分の確認を行います。**コード改修・無料検証・実API検収・稼働Botへの反映・VPS検証は、それぞれ別の状態として報告します。**
