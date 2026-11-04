# 設定変更・GitHubリリース・Rollback

**状態：改修ブランチのみ。2026-11-03時点で既存main稼働Botへの反映は禁止。** 実注文なし、private APIなし、APIキー追加なし。DigitalOcean VPSには未配備。

## 正本と設定値

戦略の正本は`app/strategy.py`、ATR倍率の正本は`app/config.py`と`app/paper.py`。2時間足EMA21/55、RSI9の55クロス+Window3、直前の確定4時間足EMA50/200、ATR14 SL×3/TP×12は変更しない。発注実装は存在しない。

`BUY_BUDGET_JPY`は`Settings.buy_jpy`に読み込まれ、仮想BUYの現金支出（板価格×数量＋想定手数料）の上限になる。現在の`MAX_BUY_JPY=50000`は絶対天井であり、13,000円へ設定した場合に50,000円で買うことは許されない。 `INITIAL_CASH_JPY`は初回schema.sqlの100,000円と一致する値に**固定**し、異なる入力は起動拒否する。既存の`portfolios`の現金やBTC残高をこの金額に書き戻したり、ポジションを初期化してはいけない。初回schema.sqlは100,000円ずつ両口座へ投入するが、再migrationでこれを上書きしない。初期資金変更は別のversioned migrationとユーザー承認が必要。

`APP_GIT_SHA`は**実際にビルド・実行したGit commitの40文字SHA**。デプロイ前に`git rev-parse HEAD`と比較し、イメージラベル・`/api/status.runtime.git_sha`が一致することを検収する。ローカルの旧リビジョンやdirty worktreeからの本番ビルドは禁止。表示専用のハードコードではなくmarket workerが実効SettingsをDBのruntime_statusへ保存する。

`PAPER_FEE_BPS`は0〜100の整数（例:25は0.25%）。`BUY_BUDGET_JPY`には手数料を含む。費用仮定の変更は新規仮想約定だけに適用し、**既存ポジションの固定SL・TPは不変**。ネットワーク切断や古い価格では評価・約定を停止する。

## リリース前のゲート（VPS用。今回実行しない）

1. リリース対象ブランチの変更差分、公開CI、レビュー、秘密ファイル・履歴検査を確認。VPSで`git status --porcelain`が空、`git rev-parse HEAD`が承認したタグ/コミットであること。
2. 既存ポジション（現金、BTC数量、entry_price、entry_cost、stop_price、target_price、realized_jpy）を読み取り専用で記録し、**バックアップと別DBへの復元**を確認。バックアップ未検証なら停止。
3. `.env`を`0600`で作成・確認し、`DRY_RUN=true`、`LIVE_TRADING_ENABLED=false`、`NEWS_MODE=mock`、`OPENAI_API_KEY`なし、`FRONTEND_PORT=8097`、`APP_GIT_SHA`が実際の確定コミット、`COMPOSE_PROJECT_NAME`が承認済みであることを確認。必要なロールパスワードは`secrets/`のファイルとしてのみ渡す。
4. 既存DBの`pg_dump`を安全に保管。`python -m app.migrate upgrade`は専用の`migration`サービスで**事前検証済みの追加migration**を実行し、既存テーブル・ポジションをリセットしない。初期化SQLを追加しただけでは既存volumeに反映されない。
5. `docker compose config --quiet`、`docker compose --profile migration run --rm migration`、`docker compose up -d --build`を**承認したプロジェクト内に限定して**行う。新DBを使う別Compose projectでテストしてから実環境へ進める。`docker compose down -v`は禁止。
6. 起動後は`docker compose ps`、実効Bind、ネットワーク、`/healthz`、認証付きGET、実効SHA・購入上限・確定足欠損・ニュースUNKNOWN、仮想口座、停止状態、既存SL/TPを再読込で比較。URLを外部公開しない。SSHトンネルの接続/切断も確認する。
7. 失敗した場合には「戻す条件」を確認し、**新規仮想購入停止**、スナップショット/証跡保存、既存DBの保全を優先する。

## ブランチとリリースの分離

今回作成した`security/do-security-hardening-20261103`を直接本番利用してはいけない。差分レビューとCI必須チェック後、ユーザーが安全なリリースコミットを明示承認してからmainへmerge/tagを切る。既存Bot`main`の稼働中イメージ、Compose project、DB、volumeはこの検証で変更していない。

バージョン更新はGitの固定コミットとコンテナイメージdigestで管理する。環境変数はユーザーの承認した変更だけ反映し、フロントからDocker操作・任意コード・任意ブランチを実行する仕組みは一切導入しない。

## 購入上限を50,000→13,000円に下げる例（今は実施しない）

```bash
# VPS配備時のみ（隔離済み、バージョン固定、バックアップ確認後）
# .envを権限0600のエディタで安全に編集: BUY_BUDGET_JPY=13000
docker compose config --quiet
docker compose up -d --no-deps --force-recreate market
# GET /api/status.runtime.buy_budget_jpy / effective_buy_budget_jpy が13000であることを確認
# 既存のportfolios.stop_price,target_price,entry_price,btc_qtyが不変であることを確認
```

`13,000円`で0.001 BTCの最低数量に届かない市場価格なら買いを見送る。現金不足・板厚不足も見送る。設定の減額はこれらの安全条件を回避しない。

## トークンローテーション

閲覧用`view_token`、管理用`admin_token`は別。各ホストの私有Vaultでのみ更新する。`python scripts/rotate_tokens.py view`または`admin`を実行し、`docker compose up -d --no-deps --force-recreate api`でAPIプロセスの新キー読込を確認する。古いトークンは失効し、既存ブラウザから再認証が必要。新しい値をCLI引数、Git、URL、ログ、ChatGPTへ貼らない。失効をローカルHTTP 401で検査し、不要にトークンを表示しない。鍵漏洩時はソース履歴も検査し、発見したキーを即時失効/再発行する。

## Rollbackの制約

1. 変更前のGit SHAとイメージdigest、`.env`（秘密は安全なVault）、DBバックアップ、migrationバージョンを記録する。
2. 障害時はまず新規仮想購入を停止し、保有の決済評価は可能な限り継続。異常だけで自動全売却はしない。
3. コードだけの互換Rollbackなら、承認済み前版を**固定SHA/tag**から再ビルドし、現在のDBと後方互換であることを検証してから対象サービスのみ再作成する。許可されていない`git reset --hard`や自動DB復元はしない。
4. DB権限migrationの`rollback-permissions`は**クローン専用**に用意した機能であり、現行DBで勝手に実行してはいけない。既存mainの単一DB管理者資格情報へ戻すことは、セキュリティ後退であり、ここで自動化しない。
5. データを戻す必要がある場合、別DBで復元試験と比較を行い、オーナーの明示承認後のみ本環境へ適用する。無条件のボリューム削除・DB初期化は禁止。

## 監査記録・制約

`admin_audit`には操作者ID、request ID、変更前後、結果・理由・時刻を記録。成功した停止/再開と監査行のcommitは同一DBトランザクション。市場ワーカーは同じPostgreSQL advisory transaction lockを購入判断で取得するため、停止完了応答後に後続のBUYがcommitする競合を防止する。これは**仮想注文のみ**の検査であり、実取引所での発注競合は未検証。

仮想市場データの収集が不完全な場合、トンネルやブラウザが動いていても購入準備は完了ではない。HTTP200と売買判定Readyを混同しない。
