# Phase 2・既存Bot統合 検収書（2026-11-06）

対象リポジトリ：<https://github.com/OPP-studio-macchino/coincheck-ai-paperbot>

統合ブランチ：`feat/ai-integration-20261106`。統合基点はmainの`a6a5f75b91b95fae0aa6c5fa25e348e0a2d5edbf`、取り込んだ公開MVPは`b2a249fb9fc5e41325c700cb0bd97d685a9ab7b5`です。実装commit、PR、CIの結果は本書末尾に記録します。以下の未確認を成功として扱いません。

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

全体試験は**149件 / 14.579秒 / PASS**。新規ファイルを含めstage後の秘密検査は`inspected=203`でPASS。iMacの全公開対象・履歴はstage後`inspected=215`、commit後`inspected=251`でPASSです。スキャナは既知形式の検出であり、あらゆる秘密の不在証明ではありません。検出時は値を表示せずfile/line/reasonだけを出力します。

iMac-2.localの新規外付けHDD worktreeでも、`TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -q`を実行しました。Python 3.15.2、2026-11-06 11:44:26 JST完了、**149件 / 11.859秒 / PASS**です。元のAI 18ファイル、security 4ファイル、BACKFILL 4ファイルは開始時の保存hashと一致し、各HEADも不変でした。元Bot worktreeはcleanのまま、既存Colima 2 profilesはStoppedのままです。

| 対象 | 実測内容 | 境界 |
|---|---|---|
| Bot回帰 | 安全flag、予算50,000/13,000、手数料/残高/板数量、固定bracket、足間隔/不足、news mock境界等の設定・純粋関数試験 | 実Coincheck接続・実残高を使わない。実DBの停止・監査・競合は4.2で別途検証 |
| 研究評価器 | 通常Botとの指標・判断の一致、UTC分割、確定足/先読み防止、遅延availability、重複/不正入力、同一cross再entry拒否、fee込み予算 | 合成データによるオフライン試験。通常Botの実DB注文処理とは別 |
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

**実CIでPASSしました。** [push CI](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/actions/runs/42152419456)と[PR CI](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/actions/runs/42152516744)はともに実装commit `4f6e70336790df643eb6ba3501f0f6762c1217b8`を対象に成功しました。PR CIは2026-11-06 11:47:12〜11:49:59 JST、検査用merge commitは`e136fc1f61406dfac243b63a93815dd8e7c355b5`です。これはmainへのmerge完了を示しません。

push CIのログから次を抽出して確認しました。生ログや資格情報は本書へ転載していません。

| 実検証 | 結果 |
|---|---|
| Python単体・結合 | 149件 / 24.542秒 / PASS |
| 公開対象・Git履歴の秘密検査 | `SECRET_SCAN_PASS` |
| 通常Composeの防御設定 | `COMPOSE_SECURITY_POLICY_PASS` |
| requirements.lockのpip-audit | 既知の脆弱性検出なし。照会したadvisoryの時点に限定 |
| アプリ・研究・生成器image | 固定base digestでbuild成功 |
| DBロールの拒否境界 | `ROLE_DENIALS_PASS` |
| 停止状態・監査の同一transaction | `PAUSE_AUDIT_TRANSACTION_PASS` |
| 処理済み足の再実行抑止 | `PAUSE_RESTART_NO_REPLAY_PASS` |
| 停止ACKと待機中購入の競合 | `STOP_ACK_RACE_PASS` |
| 口座行保持・permission rollback/re-upgrade | `PORTFOLIO_PRESERVATION_PASS` / `MIGRATION_ROLLBACK_REUPGRADE_PASS` |
| 専用DB試験全体 | `ISOLATED_DB_SECURITY_E2E_PASS` |
| 別コンテナでの候補受け渡し | PASS、1 trial、status COMPLETED、generation_mode fixture、実OpenAI呼出し0 |

実際にinspectした生成器UIDは10002、評価器UIDは10001。両方がnetwork none・read_only・cap_drop ALL・no-new-privileges・PID32・512MiB・1 CPU、read-only入力mountでした。生成器にBot・psycopg・評価器module・Docker socketがないことも実コンテナで検査しました。

この1候補の評価結果は**`NO_IMPROVEMENT`**です。試験成功は、改善しなかった候補を適切に処理できたという意味であり、利益向上を示しません。検証artifact hashは`50cf96a708bd3341f744bda798d10817d4d2971cfa6c4cf6d0a1ba6bc067e90a`です。fixtureの予約/usageは模擬値で、API支出0です。

## 5. 未実施・制約

- 実OpenAI API呼出し、実キー投入、対象プロジェクトでのmodel利用可否、実課金・請求照合は未確認。
- 実市場でのAI候補の優位性、ウォークフォワードの将来成績、paper昇格・稼働採用は未確認。
- 本作業でVPSへ接続・再配備・Firewall/SSH変更・稼働Bot再起動・既存DB migrationは行っていない。引き継ぎ時のVPS観測は[統合現況](../INTEGRATION_STATUS_20261106.md)で日時付き報告として分離。
- HTTP管理画面、SSH切断後の稼働、運用DBのバックアップ/復元、外部到達性、RPO/RTOは今回のAI統合で再検証していない。過去のセキュリティ検収を現在の結果へ読み替えない。
- container OS imageの脆弱性scannerによる検査は今回未実施。固定digestとPython依存の検査だけでimage全体の既知/未知の脆弱性が解消したとは扱わない。
- NEWS PR #2、FAST_START PR #3、BACKFILL、別dashboard/read-only Gatewayは引き継いだ継続案件で、この統合には自動取り込みしない。
- 評価imageには研究パッケージのコードが入るが、キー・通信・Bot DB認証は持たない。生成器imageは評価器・Bot・DB driverを含まない。ホスト侵害までコンテナで完全隔離できるとは説明しない。
- 独立承認と最新HEADの必須CIを満たすまでmainへmergeしない。保護解除・自己承認・管理者bypassを行わない。

## 6. 運用・戻し方

要求書作成、fixtureの初期化/生成/評価、live有効化条件、秘密ファイル、単一台帳、状態保存、失敗調査、前版へ戻す手順は[運用手順](OPERATIONS.md)を参照してください。通常Botの設定変更はGit差分・テスト・固定リリースを通します。既存ポジションの固定SL/TPを設定更新で再計算しません。

生成処理の異常時は新規生成を止め、要求hash・receipt・監査・versionを秘密を含めず保全します。RESERVED/FAILED/成果物欠落の要求を自動再送しません。キー漏洩時は失効・再発行して範囲を定めた隔離とクリーン復元を行います。台帳削除、残高初期化、全売却、volume削除で復旧しません。

コード統合のrollbackはrevert PRで行い、公開履歴・研究台帳・既存Bot DBを保持します。今回は稼働環境へ反映しないため、コードを戻すために既存コンテナを再起動する必要はありません。

## 7. 公開・最終確認記録

| 対象 | 記録 |
|---|---|
| security診断のcommit | `a45a4e1ee11ef3df3a9f80993fb7f6ddd0ceb5c7` |
| Phase 2実装・統合commit | `4f6e70336790df643eb6ba3501f0f6762c1217b8` |
| push確認 | `git ls-remote`の対象branch SHAがローカルHEADと一致。commit後worktree clean |
| 統合PR | [PR #4](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/pull/4) |
| 実装差分 | [main…4f6e703](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/compare/a6a5f75b91b95fae0aa6c5fa25e348e0a2d5edbf...4f6e70336790df643eb6ba3501f0f6762c1217b8) |
| 検証結果追記 | 本検収書への証拠追記は文書のみの別commit。最新SHA・必須CI・独立承認はPRで確認する |
| main merge | 本記録時点で未merge。GitHubの承認1名・最新push承認・必須security・会話解決が必要 |
| 稼働環境への反映 | 未反映。既存Bot・既存DB・VPSへの変更なし |

この検収書へ証拠を追記した後も、最新PR HEADの必須CIを確認してから承認対象を提示します。コード改修、隔離検証、GitHubでの承認/merge、実API成功、稼働反映はそれぞれ別の状態です。
