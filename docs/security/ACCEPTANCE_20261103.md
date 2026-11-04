# Coincheck BTC/JPY Paper Bot — DigitalOceanセキュリティ改修 検収書

検収基準日: 2026-11-03 JST / O.P.P Studio / **実注文ゼロ・有料API検証ゼロ**。
対象ブランチ: `security/do-security-hardening-20261103`。**既存の稼働Bot/main/VPSへは未反映**。

## 1. ベースラインと隔離

- 2026-11-03 10:41 JST、RDCからiMac-2.localを実読。稼働元 `/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot/` のHEADは `9ab6bf79fb07342ae6026e332d0a2b72081d6cce`、未コミット変更0。Compose project `coincheck-ai-paperbot` の db/API/market/news/frontend 全5サービスUp（db/API healthy）、host公開はfrontend `127.0.0.1:8097`のみ。
- 外付けHDDの`coincheck-ai-paperbot-security/`を別Git worktreeに作成。別ブランチ・Compose project `coincheck-ai-security`・Colima profile `coincheck-sec-test`（Dockerデーモン自体が別）・DB volume `coincheck-ai-security_dbdata`・frontend `127.0.0.1:18098`。
- Docker画像を再ビルド、隔離DBにversioned migrationを適用。隔離DBはPostgreSQL 16.15-alpine。Dockerネットワーク `db_private` と `front_edge` は**実構成で`internal:true`**、DB/APIのホスト公開ポートなし、frontendのみ127.0.0.1:18098。unprivileged NginxはDocker localhost公開のため別の`loopback_transport`へ参加（frontendとDBが同ネットワークにいない）。
- 元の稼働DBからの**読み取り専用`pg_dump -Fc`**を、隔離DBサーバーの`paperbot_restore_20261103`へパイプ復元。元DBは変更せず、収集件数が約定受信に伴って増え続けたことを確認。復元クローンはportfolio2件、tick件数はスナップショット取得時3904件。複製後に元DBのtickが3918件となったことも観測（停止なし）。

## 2. 戦略固定条件

数値は現行`app/strategy.py`、`app/paper.py`を先に実読した。**EMA21/55、RSI9の55上抜け・Window3、直前の確定4時間足EMA50/200、ATR14・SL×3/TP×12**を維持。指標値や売買ルールを利益最適化のため変更していない。Pythonコンフィグに上限50,000円と固定の戦略版、初期100,000円を定義し、実効値を`runtime_status`へ市場ワーカーが保存。未確定足を購入判定に使用せず、必要な連続2時間足200本/4時間足300本に未達なら理由つきで停止。上位4時間足は既知の**確定値のみ**。

## 3. コード差分・主な防御

| 対象 | 改修 |
|---|---|
| 認証 / 権限 | view/admin独立のランダムトークン、全`/api/*`にBearer必須、認証なしhealthは低情報量。viewのPOST拒否、Origin/Host・body型/長さ・Host制限、in-memory認証失敗制限、クライアント偽装転送元ヘッダーを信頼しない |
| 監査 / 競合 | `admin_audit`へactor/request ID/前後状態/時刻/結果/理由。成功する停止POSTと監査INSERTは単一トランザクション。`pg_advisory_xact_lock(713446)`を停止/購入の双方で取得。停止完了後のBUY commitを防止 |
| DB権限 | schema/migration専用権限、market/news/query/control別資格情報、最小限のGRANT。newsは資産更新不可、view接続はSELECT専用、controlは停止値更新と監査追記だけ、ランタイムDDL禁止 |
| 資金 | `Settings.buy_jpy`で新規BUYの上限、`MAX_BUY`は絶対天井。**手数料込み**最大50,000/13,000円。初期100,000円を既存残高へ再投入しない。既存SL/TPは書換えない |
| 市場 | トレードID`ON CONFLICT DO NOTHING`、同一足`UNIQUE(portfolio,bar_start)`、トランザクション＋行ロック、同RSIクロス窓内の再購入抑止。収集切断・過去価格時の判定停止 |
| ニュース | mock/unknownはnews_gateを抑止、独立baselineは仕様維持。AI提供URL/ドメインだけで`clear`としない。任意URL取得・任意コード・秘密読み取り・注文権限なし |
| Docker | DB `internal:true`、frontendはDBへ直接接続不可。Python非root、read_only rootfs、no-new-privileges、cap_drop ALL、PID/メモリ/CPU制限、Docker socketなし |
| フロント | 実資産未接続を明示。仮想JPY/BTC、取得原価・平均単価・固定SLTP・実現損益・判断／仮想約定履歴。Git SHA・戦略版・有効購入予算・ニュース/モード・市場時刻・確定足数/理由・停止状態・決済評価可否。価格が古いと時価/含み損益非表示 |
| CI / 秘密 | secret値を出力しないGit現行/履歴走査、最低権限Workflow・SHA固定Actions、依存性検査、Pythonテスト、Compose安全設定確認。実秘密は各専用ファイルに保存し、Git・Dockerイメージから除外 |

## 4. 検査結果（確認できた事実のみ）

| No. | 検証項目 | 結果 | 証跡と制約 |
|---|---|---|---|
| 1 | DRY_RUN解除/LIVE有効化/Coincheck private keys拒否 | **PASS（単体）** | Settings各拒否テスト、注文モジュールなし |
| 2 | 全保護GET/POST認証とviewerによるPOST拒否 | **PASS（HTTP）** | 無認証401、viewer GET200/POST403、admin POST200 |
| 3 | 停止再開監査・並行BUY競合・復帰後状態 | **PASS（隔離DB）** | audit前後値、同一トランザクション、stop-ACK並行購入SKIP、停止状態を初期値へ戻す |
| 4 | DB各ロールの許可拒否境界 | **PASS（隔離DB）** | query書換え拒否、news資産/filled行更新拒否、control資産/market更新拒否、全runtime DDL拒否 |
| 5 | 50,000/13,000円手数料込み予算・無効値/残高/最小数量 | **PASS（単体＋独立fixture DB）** | 買付合計上限内、20,000,000円/BTCでは13,000円を見送り |
| 6 | 停止/news unknown時の新BUY抑止と保有ATR決済 | **PASS（fixture DB）** | pause中のSTOP仮想売却、news_gate購入SKIP、baseline独立 |
| 7 | 通信断・古い価格・DB障害・足欠損 | **部分PASS** | 純関数・構文・fail-closed判定。実通信故障/DB停止時の完全E2Eは未実施 |
| 8 | 再起動・重複配信・並行処理の二重約定防止 | **PASS（fixture DB）** | 同じtrade_id 2回保存なし、接続切替でも重複なし、同足並行BUYで1件だけ成功、同RSI窓で再BUYなし |
| 9 | エラー・秘密検査失敗時の秘密非出力 | **部分PASS** | 走査はファイル名・行番号・分類のみ、実秘密をログへ出さず静的検査。全障害経路は未実施 |
| 10 | 複製DB復元/既存DB非破壊migrationとRollback | **PASS（クローン）** | 本番稼働DBをread-only pg_dump→`paperbot_restore_20261103`へ復元。適用・権限Rollback・再適用でportfolio内容のMD5 `56dab92976f06360ec370f8acd1724f4`が不変 |
| 11 | 管理待受・操作・SSH切断後継続 | **部分PASS** | localhost:18098のGET/POSTと5サービス稼働確認。実VPS/SSHトンネル切断試験は未実施 |
| 12 | 実注文/private API/有料OpenAI実呼出なし | **PASS（試験範囲）** | market公開取引・板のみ、mock news、実注文アダプターなし。有料APIキー投入なし |

**自動テスト：32件PASS**（元18件を含む）。Python構文・JavaScript構文・Compose構文PASS。独立HTTPスモークはview/admin GET/POSTと元pause状態への復元PASS。暗号化バックアップ生成PASS。2026-11-03の`pip-audit==2.9.0`では固定Pythonパッケージに既知の脆弱性なし（検出能力・今後の新規CVEまで保証しない）。

## 5. 明示的な未完了・残余リスク

- **VPS未作成・未配備**。SSHホスト鍵検証、Cloud Firewall/UFW/IPv6外部到達性、一般管理ユーザーとroot/パスワード無効化、実SSH切断継続、WireGuard追加は未検証。
- 管理APIがquery資格情報とcontrol資格情報を**同一プロセスで保持**するため、プロセス侵害から完全隔離できるとは言わない。
- ニュースの独立した一次資料実取得は未実装。現在`NEWS_MODE=mock`なので`news_gate`は常に買いを抑止。live APIの安全/有料試験は今回行っていない。
- 同時間内のローソク足の完全性（短時間断線時の全約定復元、取引所実約定、板更新の競合、過去データ修復）は未実証。戦略一致はコード照合・単体のみでありCoincheckの50日分連続データ検証はまだできていない。
- AESなどのイメージ実行ベース脆弱性スキャン、実DigitalOcean外部ネットワークペンテスト、予期しないAPI障害/DB障害の全面フォールトインジェクションは未実施。
- 暗号化バックアップの**生成**はPASS、ageオフライン復号鍵を利用した`restore_clone.sh`での復元は未実施。鍵をプロジェクト外へ移動する操作は安全チェックで拒否されたため、保護を迂回していない。VPS外コピーも未実施。RPO/RTO未計測。
- このブランチのGitHub Actionsは実際のpush後に実行して結果を確認する。mainのbranch protection/API必須CIルールは別途設定確認が必要。必要な権限や有効なルールがない場合は手動対応事項として残す。
- Build imageに入る`requirements.lock`は全依存の固定バージョン。各distributionのハッシュ/マルチプラットフォーム再現性は追加検査対象。Docker/Compose・GitHub ActionsのSHA/digest固定を検収する。

## 6. 再実行コマンド（隔離環境専用）

```bash
cd "/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-security"
python3 -B -m unittest discover -s tests -q
python3 -B scripts/scan_secrets.py --history
docker-compose config -q
export DOCKER_CONTEXT=colima-coincheck-sec-test
docker-compose ps
SECURITY_ISOLATED_TEST=YES_CLONE python3 -B scripts/smoke_isolated.py
# DB role/migrationテストは既存のfixtureに重ねると状態が残る。
# 新しいpaperbot_test_* DBを明示的に作ってmigrationを適用してから実行。
```

2026-11-03以降のバグ修正・Docker image rebuild・GitHub push後、最終テスト・SHA・CI結果をこの検収書または別の追記に記載する。

## 7. 出荷判定

**改修コードを隔離worktreeに実装し、既存Botへ未反映。DigitalOceanリリース判定：NO-GO（未配備・復元/SSH/ネットワーク外部検収が残る）。** スコープはDRY_RUN専用。検収済みの項目と未確認項目は混同しない。
