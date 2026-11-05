# AI自己改善エンジン 実行・運用手順

対象はオフライン研究MVPです。[正本](SOURCE_OF_TRUTH.md) の権限範囲を維持します。
macOS / Linux、Python 3.12を標準とします。追加pip依存とAPIキーは不要です。
これは既存Botのアップデート手順ではありません。

## 1. 作業場所を確認する

今回のiMac隔離worktree：

```text
/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-ai-lab/
```

既存稼働Botの `coincheck-ai-paperbot/`、セキュリティ検証用の `coincheck-ai-paperbot-security/` と区別します。
ブランチは `feat/ai-self-improvement-mvp-20261105` です。

```bash
cd "/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-ai-lab"
git status --short --branch
git rev-parse HEAD
python3 --version
```

全てのコマンドをこのworktreeで実行します。稼働Botの `.env` をsourceしたり、秘密をコピーしたりしません。

## 2. 最小のデモを実行する

```bash
python3 -m unittest discover -s tests -q
python3 -m ai_lab demo \
  --workspace runtime/ai_lab \
  --run-id demo-50k \
  --max-trials 6 \
  --buy-budget-jpy 50000
```

180日分の合成5分OHLCVを生成し、50日の準備期間の後、時間順に学習・開発・最終評価へ分割します。
初期仮想資金は100,000円、購入支出の上限は50,000円です。
合成価格で得た利益は、Coincheckでの過去利益・期待利益ではありません。

結果は同じ専用ディレクトリに残ります。

| ファイル | 内容 |
|---|---|
| `research.sqlite3` | 実験・試行・最終期間使用記録・監査 |
| `inputs/synthetic-180d-seed47.csv` | 決定論的に生成した合成入力 |
| `demo-50k.json` | 実験の保存済みスナップショット |
| `demo-50k.html` | ブラウザで読む静的な結果画面 |

Macでは `open runtime/ai_lab/demo-50k.html` でローカルファイルを開けます。
HTTPサーバ、公開ポート、フロントのログイントークンは不要です。
画面には保存時点の研究結果を表示します。現在の相場・口座・Bot運転状態ではありません。

13,000円の試験は別IDで実行します。

```bash
python3 -m ai_lab demo \
  --workspace runtime/ai_lab \
  --run-id demo-13k \
  --max-trials 6 \
  --buy-budget-jpy 13000
```

同じ最終評価期間が先の実験ですでに消費されていれば、後の実験で最終評価をやり直しません。
試験用に初期状態が必要な自動テストは一時ディレクトリを使います。実研究で都合のよい結果を得るため台帳を作り直してはいけません。

## 3. CSVを使って研究する

必須列は次の6列です。`source`、`available_at` は任意ですが、可能な限り記録します。

```csv
timestamp,open,high,low,close,volume,source,available_at
2024-01-01T00:00:00+00:00,6000000,6010000,5990000,6005000,1.25,coincheck,2024-01-01T00:05:00+00:00
```

この1行は列の説明用で、研究に必要な期間を満たすデータではありません。

- `timestamp` はUTCの足開始時刻です。秒・ミリ秒の数値やJST文字列を自動推定しません。
- 全行は正確に5分間隔で並び、価格は正、出来高は0以上にします。
- `source` は `synthetic`、`bitbank`、`coincheck`、`unknown` のいずれか。混在は拒否します。
- `available_at` は、その足の情報が利用可能になったUTC時刻。省略時は足終了時刻と仮定します。
- hashはファイルの同一性を確認します。`source=coincheck` という文字列だけで真のCoincheck履歴と認定しません。
- 対象がBTC/JPYであることは利用者が確認します。MVPにはペアと収集時刻の列はなく、`available_at` は実際の収集時刻の証明ではありません。`--as-of` は入力検査に使いますがmanifestへは保存しません。
- 各種の不正値・欠損・重複・不完全足は修正せず拒否します。

まず、研究と独立して検査できます。

```bash
python3 -m ai_lab validate-data \
  --csv /absolute/path/to/frozen_btcjpy_5m.csv \
  --as-of 2026-11-05T00:00:00Z
```

`--as-of` はファイルを凍結・確認した時点へ置き換えてください。未来の時刻は使用できません。
時刻を省略すると、実行時刻までに確定済みかを検査します。

```bash
python3 -m ai_lab run \
  --workspace runtime/ai_lab \
  --run-id research-001 \
  --csv /absolute/path/to/frozen_btcjpy_5m.csv \
  --max-trials 8 \
  --buy-budget-jpy 50000
```

最終区間の結果は候補生成へのフィードバックに入りません。
合格候補が出ても自動適用されず、人が結果を確認する状態で終了します。
今回のMVPには「実OpenAI providerへ切り替えるコマンド」はありません。

## 4. 同じ実験を再実行・確認する

```bash
python3 -m ai_lab status --workspace runtime/ai_lab --run-id demo-50k
```

statusは保存済み結果からJSONとHTMLを再生成します。相場評価や候補生成は行いません。

完了したデモを同じコマンド・同じコード・同じデータで実行すると、保存済み結果を返します。
データ、購入予算、試行上限、ポリシー、正本、評価器コード等を変えた場合は、同じ実験IDの再開を拒否します。
新しいIDで研究する場合も、過去に消費した最終期間を再利用できるとは限りません。

途中で終了した場合、同じコマンドで再開します。
予約だけ残った生成試行は失敗として数え、生成済み候補は同じ候補を再評価します。
最終評価の予約が残った場合、その期間は消費済みです。結果が残っていなくても、未使用へ戻しません。

## 5. Dockerで通信を遮断して実行する

独立したComposeファイルだけを指定します。既存BotのComposeと `-f` を重ねません。
今回のiMacで利用した検証用contextは `colima-coincheck-sec-test` です。
これは既存セキュリティ検証とDocker engineを共有しますが、project・volume・コンテナは別です。
作成・起動済みの確認は [検収書](ACCEPTANCE_20261105.md) を参照してください。

```bash
DOCKER_CONTEXT=colima-coincheck-sec-test \
  docker-compose -f compose.ai-lab.yaml -p coincheck-ai-lab-mvp config --quiet

DOCKER_CONTEXT=colima-coincheck-sec-test DOCKER_BUILDKIT=0 \
  docker-compose -f compose.ai-lab.yaml -p coincheck-ai-lab-mvp build

DOCKER_CONTEXT=colima-coincheck-sec-test \
  docker-compose -f compose.ai-lab.yaml -p coincheck-ai-lab-mvp run --rm research \
    demo --workspace /var/lib/ai-lab/research --run-id docker-demo --max-trials 6
```

上の `docker-compose` は今回のiMacのCLIです。Docker Compose pluginがある環境では `docker compose` に読み替えます。
このiMacではclassic builderを使用しました。BuildKitが利用できる環境では `DOCKER_BUILDKIT=0` は不要です。どちらのbuilderでも、正本1ファイルを含み、秘密・入力データ・台帳を含まないbuild contextを使います。
`network_mode: none`、秘密mountなし、公開ポートなしのone-shot処理で、終了後は研究用volumeだけが残ります。
VPSへこのコマンドを実行することは今回の作業範囲に含まれません。

Docker内の結果を見る場合は、同じvolumeを使った研究コマンドでstatusを表示できます。

```bash
DOCKER_CONTEXT=colima-coincheck-sec-test \
  docker-compose -f compose.ai-lab.yaml -p coincheck-ai-lab-mvp run --rm research \
    status --workspace /var/lib/ai-lab/research --run-id docker-demo
```

HTMLを取り出す場合は対象の研究コンテナだけを作成し、`docker cp` で研究結果をコピーしてからその停止済み研究コンテナを除去します。
研究volumeやBotのvolumeを削除する必要はありません。

## 6. 停止・障害対応

研究はCtrl-Cで停止できます。停止は既存Botの新規購入停止やポジション決済とは別の操作です。
既存Botの管理APIを呼び出す必要はありません。

| 表示・状態 | 対応 |
|---|---|
| `RESUME_MANIFEST_MISMATCH` | 何が変わったか比較する。旧実験を上書きせず、新IDを使う |
| `HOLDOUT_ALREADY_CONSUMED` | 再利用しない。未使用の将来データを待つ |
| `RESEARCH_ALREADY_RUNNING` | 先行プロセスを確認する。ロックを無条件削除しない |
| `WORKSPACE_NOT_RESEARCH` | 出力先が既存Botや無関係なディレクトリでないか確認する |
| `TIME_LIMIT` | 1回の研究評価処理の時間上限で停止。入力サイズと処理状況を確認する |
| ディスク・SQLite・監査エラー | 研究を止め、台帳と入力を保全し、コピーで調査する |
| 全候補不採用 | 正常な結論。採用条件を結果に合わせて緩めない |

600秒の時間上限は、入力検査とmanifest確認の後に始まる1回の研究評価処理に適用します。
CSV取込・起動・レポート出力を含むプロセス全体のwall time、手動再開を含む累積稼働時間、RTOを保証しません。
候補生成回数は台帳で累積管理し、再開によって予算を補充しません。

## 7. 研究台帳の保存

研究を停止した状態で、専用ディレクトリ全体を保全してください。
入力CSV、SQLite台帳、JSON、レポートと、使用したGit SHA・コードhashの対応を保持します。
実行中のSQLiteを単純コピーせず、停止するかSQLiteのbackup APIを使います。
研究に使った実データを含む保存物はpublic GitHubへ追加しません。

最終期間の消費記録は過学習を抑えるための履歴でもあります。古い実験を都合よく消して評価期間を再利用しないでください。
ホスト侵害や管理者による台帳置換まで防ぐ仕組みではないため、運用上の保全とレビューが必要です。

## 8. 設定変更とリリース

購入予算を変える場合は別実験IDを使います。policy、DSL、採否基準の変更はコードレビューと正本更新を通します。
研究結果だけを見て稼働Botの `.env`、Python、DB、ポジションを直接変更しません。

今回の提出は改修ブランチです。mainへのマージ、VPS配置、実OpenAI利用、実売買への移行は別の作業です。
前の版へ戻す場合は、旧SHAの**別worktree/別研究領域**で検証します。旧版が新しい台帳を更新することを避けます。
既存Botの戻し方は、既存の [固定リリース・ロールバック手順](../security/CONFIG_RELEASE_ROLLBACK.md) を参照します。
