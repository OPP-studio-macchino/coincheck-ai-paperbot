# Phase 2・既存Bot統合 検収書（2026-11-06）

対象リポジトリ：<https://github.com/OPP-studio-macchino/coincheck-ai-paperbot>

統合ブランチ：`feat/ai-integration-20261106`。統合基点はmainの`a6a5f75b91b95fae0aa6c5fa25e348e0a2d5edbf`、取り込んだ公開MVPは`b2a249fb9fc5e41325c700cb0bd97d685a9ab7b5`です。最終commit、PR、CIの結果は本書末尾で追記します。以下の未確認を成功として扱いません。

## 1. 変更の目的と範囲

既存BotとAI研究機能の開発を同じリポジトリへ集約します。元のPhase 2作業には、生成候補を実評価器へ渡す契約、失敗を含む費用の永続管理、途中中断後の再送防止について補強が必要でした。元worktreeの未コミットファイルをhash付きで外付けHDDへ保全し、別worktreeで改修しました。

今回の成果は、凍結CSVから要求書を作成し、専用プロセスで1候補を保存し、通信と秘密を持たない評価器で照合・評価する一連のコードです。既定はfixtureで、実OpenAI APIへの成功をこの検収では示しません。実AIによる連続改善ループ、自動採用、Botの設定自動変更、Git/Docker/注文の自動操作は追加していません。

| 変更 | 解決する問題・結果 |
|---|---|
| `prepare-proposal` / `evaluate-artifact` | dataset、分割、設定、policy、ソース、prompt/schemaと候補を結び付ける。評価側で同じCSVから要求書を再構築 |
| 専用生成器 | 固定Responses宛先・model・出力上限・service tier、toolsなし、再試行なし、要求/応答サイズ制限、TLSと全体deadline |
| 費用台帳 | 初期化を明示操作に限定。呼出し前に日次/累積の金額・回数を原子的に予約。失敗・中断時も予約を保持 |
| 成果物保存 | 上書きしない原子的保存、ファイル権限・symlink検査、厳密schema、候補/要求/receipt照合 |
| 再開時の検査 | GENERATEDとCOMPLETEDの保存候補が元artifactと一致することを確認。名前だけの差し替えも拒否 |
| Docker / CI | 生成器と評価器を別コンテナ・UID・台帳で実行。fixtureは両方network none。新規CI DBだけで権限/停止/migration回帰 |
| migration診断 | 生の例外・SQLを表示せず、固定stage・例外型・限定ASCII SQLSTATEだけを返す |
| 文書 | 正本・計画・技術をv0.2へ整合。運用、復旧、設定変更、過去報告と今回の確認を区別 |

## 2. 保持したBot契約

`DRY_RUN=true`、`LIVE_TRADING_ENABLED=false`、`NEWS_MODE=mock`を維持します。Coincheck private API・実注文・実資産接続・Coincheck秘密鍵追加はありません。

通常Botの戦略は、確定2時間足EMA21/55、終値>EMA21>EMA55>EMA55[-3]、RSI9の55上抜けage 0〜3かつ現在値55以上、確定4時間足の終値>EMA50>EMA200、ATR14・SL3/TP12です。通常Botの`app/strategy.py`、資金/決済、API/UI、DB schema/migrations、通常`compose.yaml`の変更はありません。`app/`の今回の差分はmigration診断だけです。

手数料を含む購入上限50,000円と、13,000円等の低い実効設定を維持します。初期資金100,000円は購入上限と別です。baselineとnews_gateの独立仮想口座、既存ポジションの固定SL/TP、残高は変更していません。研究用DSLの候補値は通常Botへ適用されません。

## 3. 費用と権限の境界

固定料金版に基づく1試行の予約は2,111,536 microUSD（2.111536 USD）です。モデル最大入力1,047,576 tokensと出力2,048 tokensに通常単価を適用した保守的な上限予約であり、実際にこの金額を支出したという意味ではありません。入力バイト数や呼出側の過小な自己申告見積りを信用しません。根拠と期限は[正本](SOURCE_OF_TRUTH.md)と`ai_lab/phase2_policy.py`を参照してください。

成功後も予約を返還しません。失敗・timeout・COMMIT前後の中断、前日の未確定費用、前日予約の当日完了を扱います。日次上限だけでなく累積上限と回数を固定し、同じ要求の再送を拒否します。`actual_microusd`は応答usageから通常単価で算出した保守的な換算値で、請求照合結果ではありません。fixtureのusageは明示的な模擬値です。

この統制は一つの保全された状態領域に対するものです。台帳を削除・複製して別に実行する管理者、ホスト侵害、他のキー/プロセスによる支出、将来の料金変更まで防ぐ口座全体の課金上限とは説明しません。liveの新規予約は料金確認期限2026-12-06 00:00 UTC以降に停止します。fixtureは外部通信なしで期限後も試験可能です。

生成器には注文・Bot DB・Docker socket・Git/deploy・任意コード実行の権限を与えません。生成器へ送る情報はtraining/development文脈に限定し、holdoutの実データ・評価値は送りません。artifactのhashは内容の結び付きであり、電子署名や実API由来・実請求の独立証明ではありません。

## 4. 今回の検証と証拠

### 4.1 ローカル隔離worktreeで実測済み

2026-11-06、Python 3.12.14で以下を実行しました。

```bash
python3 -B -m unittest discover -s tests -q
python3 -B scripts/scan_secrets.py --history
git diff --check
```

全体試験は**149件 / 14.579秒 / PASS**。秘密検査はこの時点の追跡ファイルと到達可能な履歴でPASSです。新規ファイルはstage後にも全公開対象を再検査します。スキャナは既知形式の検出であり、あらゆる秘密の不在証明ではありません。検出時は値を表示せずfile/line/reasonだけを出力します。

| 対象 | 実測内容 | 境界 |
|---|---|---|
| Bot回帰 | 安全flag、予算50,000/13,000、手数料/残高/数量、停止、足/UTC/先読み、重複/遅延等の既存テスト | 実Coincheck接続・実残高を使わない |
| 費用台帳 | 4プロセスの競合、同じ要求の並行予約、COMMIT前後の`os._exit()`、再起動、日跨ぎ、監査rollback、権限/ファイル置換 | SQLiteの実ファイルとプロセスを使用。外部請求は模擬 |
| 生成器 | 送信前予約をspyで確認、未初期化/opt-in/秘密混入拒否、失敗・refusal・不完全応答、送信/保存中断後の再送拒否 | 固定応答・spy。実APIは呼ばない |
| HTTP境界 | 固定宛先/要求、redirect拒否、重複JSON/NaN/過大応答、TLS接続条件、実タイマーによる全体deadline | fake transport。実TLS接続の到達性は未確認 |
| 成果物→評価 | fixture生成・費用予約・保存→実オフライン評価器、異なるデータ/設定/ソース/候補の拒否 | 合成価格。利益改善の証明ではない |
| 再開改変 | GENERATEDの別候補、COMPLETEDの名前だけの変更、異なるattempt IDを拒否 | 内容hashを再計算した改変も含む |
| 秘密秘匿 | CLI引数、HTTP/SQLite例外、migration診断にcanaryを入れ出力を検査 | 実秘密を投入しない |
| 文書/構文 | 正本7文書のリンク/フェンス/空白、Python AST、Compose/CI YAML構文 | 実Docker起動の代用にしない |

検証時の研究ソースhashは`3cd840b5162e20a7fa2a426e3ff5edba33f5da6d1f0a0d05e857eb3fecfdb429`、baseline hashは`101c5731135f16f74c5675a4f496ab4a504041b826ef4e72192ba60e2f1eeaae`です。研究ソースhashは`ai_lab/*.py`、policy、Bot strategy、正本文書から算出し、Git commitとは別に記録します。

### 4.2 実Docker / PostgreSQLの検証

専用CI runnerに、新しいCompose project `coincheck-ai-security-ci`、新規DB volume、公開ポートなしのDBを作る試験を追加しました。通常のmarket/news/frontendは起動しません。schema→migration→`app.security_e2e`により、各ロール、停止監査・競合、既存行保持、非破壊のpermission rollback/re-upgradeを検査します。これは運用DBの複製・バックアップ復元試験とは別です。

`scripts/verify_ai_isolation.py`は、その呼出しだけの固有名の生成器/評価器を起動し、実際のnetwork none、非root、read_only、cap_drop、no-new-privileges、PID/メモリ/CPU制限、入力のread-only mountを`docker inspect`で確認します。fixtureの同じ要求を2回渡して予約1件を確認し、別コンテナの実評価器へ渡します。評価プロセスのOpenAI呼出しは0です。既存のDB・volume・コンテナは対象にしません。新規fixture volumeを既存volumeと同一視しません。

**この節のCI実行結果は公開後に追記します。構成と試験コードの作成だけで実行済みとは扱いません。** 既存iMacのColima profilesは開始時Stoppedであり、本作業で起動しません。

## 5. 未実施・制約

- 実OpenAI API呼出し、実キー投入、対象プロジェクトでのmodel利用可否、実課金・請求照合は未確認。
- 実市場でのAI候補の優位性、ウォークフォワードの将来成績、paper昇格・稼働採用は未確認。
- 本作業でVPSへ接続・再配備・Firewall/SSH変更・稼働Bot再起動・既存DB migrationは行っていない。引き継ぎ時のVPS観測は[統合現況](../INTEGRATION_STATUS_20261106.md)で日時付き報告として分離。
- HTTP管理画面、SSH切断後の稼働、運用DBのバックアップ/復元、外部到達性、RPO/RTOは今回のAI統合で再検証していない。過去のセキュリティ検収を現在の結果へ読み替えない。
- NEWS PR #2、FAST_START PR #3、BACKFILL、別dashboard/read-only Gatewayは引き継いだ継続案件で、この統合には自動取り込みしない。
- 評価imageには研究パッケージのコードが入るが、キー・通信・Bot DB認証は持たない。生成器imageは評価器・Bot・DB driverを含まない。ホスト侵害までコンテナで完全隔離できるとは説明しない。
- 独立承認と最新HEADの必須CIを満たすまでmainへmergeしない。保護解除・自己承認・管理者bypassを行わない。

## 6. 運用・戻し方

要求書作成、fixtureの初期化/生成/評価、live有効化条件、秘密ファイル、単一台帳、状態保存、失敗調査、前版へ戻す手順は[運用手順](OPERATIONS.md)を参照してください。通常Botの設定変更はGit差分・テスト・固定リリースを通します。既存ポジションの固定SL/TPを設定更新で再計算しません。

生成処理の異常時は新規生成を止め、要求hash・receipt・監査・versionを秘密を含めず保全します。RESERVED/FAILED/成果物欠落の要求を自動再送しません。キー漏洩時は失効・再発行して範囲を定めた隔離とクリーン復元を行います。台帳削除、残高初期化、全売却、volume削除で復旧しません。

コード統合のrollbackはrevert PRで行い、公開履歴・研究台帳・既存Bot DBを保持します。今回は稼働環境へ反映しないため、コードを戻すために既存コンテナを再起動する必要はありません。

## 7. 公開・最終確認記録

この欄はcommit/push、iMacでの再実行、実CIの結果が確認できた後に追記します。main merge・稼働反映・実API実行を推定で完了扱いしません。
