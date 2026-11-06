# Coincheck Bot・AI研究機能 統合現況

基準日：2026-11-06（日本時間）
対象：[OPP-studio-macchino/coincheck-ai-paperbot](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot)
統合ブランチ：`feat/ai-integration-20261106`

この文書は既存Bot、AI自己改善エンジン、関連画面・運用の開発入口です。権限・候補仕様の正本は[AI正本v0.2](ai-self-improvement/SOURCE_OF_TRUTH.md)、コードの検証結果は[Phase 2検収書](ai-self-improvement/ACCEPTANCE_PHASE2_20261106.md)に記録します。本書だけでmerge、実API成功、稼働反映の完了を示しません。

## 1. 今回の統合範囲

既存Botと研究機能の開発・保守を一つの窓口へ集約します。同じリポジトリ内に置いても、研究用台帳、API費用、実行プロセス、秘密、資金の権限を分離します。

| 対象 | 扱い |
|---|---|
| 公開AI MVP | 最新mainのセキュリティ改修を基点に取り込む |
| 未コミットPhase 2 | 出自を保全し、費用統制と成果物評価の不足を改修して統合 |
| migration診断修正 | 生の例外・SQL・秘密を出さない段階別分類として取り込む。DB変更の実行とは別 |
| 通常Bot戦略、口座、停止・決済、認証、DBロール | 現行契約を維持し、差分と回帰試験を確認 |
| 通常Compose、稼働コンテナ、既存DB、VPS | このコード統合で更新しない |
| NEWS / FAST_START / BACKFILL | 継続案件として記録し、個別の取り込み・有効化判断へ残す |
| 独立dashboard / read-only Gateway | 別リポジトリとして保守を引き継ぐ。今回のmerge対象外 |

通常Botは`DRY_RUN=true`、`LIVE_TRADING_ENABLED=false`、`NEWS_MODE=mock`です。実注文・Coincheck private API・秘密鍵の追加は行いません。手数料込み購入上限50,000円、13,000円等の低い設定、独立したbaseline/news_gate、既存の固定SL/TPと残高を保持します。

## 2. 情報の時点と根拠

| 区分 | 内容 |
|---|---|
| 引き継ぎ元の読み取り報告 | 2026-11-06 10:48〜10:55 JST。Git、GitHub、API、ファイルmetadata等 |
| 統合担当の開始時再確認 | 2026-11-06 11:11 JST。iMacの6 worktree、GitHub main・PR・保護設定、Colima状態 |
| 過去の検証証跡 | 11/3〜11/5のセキュリティ・復元・MVP検収書とActions |
| 今回のコード検証 | 実行コマンド、対象SHAまたは作業ツリー、日時、結果をPhase 2検収書で管理 |
| 実API・VPS現況の独立再検証 | 本統合で実施した範囲だけを検収書へ追加。過去報告から補完しない |

開始時再確認後の変更もあるため、ここにあるSHAを今後の現在値と決めつけず、リリース前に再確認します。公開文書に秘密値、実DB、残高一覧、全文ログ、秘密入りアーカイブを含めません。

## 3. 統合開始時のGit状態

| 領域 | branch / commit | 開始時の扱い |
|---|---|---|
| GitHub main | `main` / `a6a5f75b91b95fae0aa6c5fa25e348e0a2d5edbf` | セキュリティPR #1 merge済み。統合基点 |
| 元のBot worktree | `main` / `9ab6bf79fb07342ae6026e332d0a2b72081d6cce` | cleanだが古い。統合作業でcheckout・更新しない |
| security worktree | `security/do-security-hardening-20261103` / `2c43f17c15786fc58a839b11da436dcb18ec2f06` | commit treeはmainと同一。診断修正2ファイルと過去記録2ファイルが未コミット |
| 公開AI MVP | `feat/ai-self-improvement-mvp-20261105` / `b2a249fb9fc5e41325c700cb0bd97d685a9ab7b5` | mainの子コミット。既存Bot本体の戦略・DB変更なし |
| 元のAI worktree | `feat/ai-self-improvement-phase2-20261106` / `b2a249fb9fc5e41325c700cb0bd97d685a9ab7b5` | Phase 2の18ファイルが未コミット。HEADだけではPhase 2を取り込めない |
| NEWS | `feature/verified-live-news-paper-20261106` / `83907233056e48977bc744fba4bf08eb4bffe7d6` | [PR #2](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/pull/2)、別案件 |
| FAST_START | `feature/paper-faststart-proxy-20261106` / `8811b7b59d7870fb22e18ed9153a0fdd7b24e159` | [PR #3](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/pull/3)、別案件 |
| BACKFILL | `feature/coincheck-public-trades-backfill-20261106` / mainと同じSHA | 未追跡4ファイル。既存DBへ適用しない |

AI、security、BACKFILLの対象コード・文書は、元worktreeを変更せず外付けHDDのprivateな保存先へ個別保全しました。ファイルhashと各基点SHAを記録し、既存の秘密・runtime・DBを公開対象へ取り込みません。

終了済みC2Cの「completed」や100テストの過去報告は、現在の統合コードの独立検収ではありません。今回の結果で置き換えるべきものは検証の現況であり、元の履歴を消去することではありません。

## 4. 稼働環境の扱い

### iMac

11:11 JSTの再確認で既存`coincheck-ai`と`coincheck-sec-test`のColima profileはStoppedでした。古い「5サービス稼働中」という記録を現在の状態へ読み替えません。名前が同じcontextやvolumeを、今回用の新規検証環境として無確認で使用しません。

### VPS

10:48〜10:55 JSTの引き継ぎでは、認証付きstatus APIが以下を返したと報告されています。

- modeはDRY_RUN_ONLY、newsはmock。
- runtime SHAは`2c43f17c15786fc58a839b11da436dcb18ec2f06`。
- 戦略版は2h EMA21/55、RSI9/55/Window3、確定4h EMA50/200、ATR14/SL3/TP12。
- 市場受信はあるが、本体の必要な連続足が不足してhistory_readyはfalse。
- 実Coincheck資産は未接続。

これは引き継ぎ報告です。今回の統合担当が、稼働イメージ内容、全コンテナ、DB権限、Firewall、sshd、定期バックアップを現在確認したことにはしません。runtime SHAの表示だけで、コンテナ内ソースとの一致まで証明しません。

VPSは他案件と共用されています。既存ポート、Firewall、リバースプロキシ、Saxo/FX側の構成に、このAIコード統合から変更を加えません。Coincheck管理は127.0.0.1限定とSSHトンネルを維持します。

### 独立FAST_START

引き継ぎは、bitbank履歴をシグナルに、Coincheck公開板で仮想約定する別paper実験のcronを報告しています。本体のCoincheck履歴不足やnews_gate、AI自己改善の実稼働を証明するものではありません。既存cron・状態領域を今回変更しません。

## 5. AI v0.2の範囲と未確認

| 項目 | 本版の契約 |
|---|---|
| 既定生成 | fixture、外部通信0、キー不要 |
| Phase 2受け渡し | 要求書作成 → 1候補保存 → 同じCSV・設定から再照合して評価 |
| 実AIの権限 | bounded DSL候補だけ。注文、Bot DB、Git、Docker、任意コードの道具なし |
| 費用 | 固定model/料金版から送信前原子予約。日次・累積の金額/回数を固定 |
| 障害 | 失敗・timeout・crashも予約維持。同じ要求を再送しない |
| 評価 | evaluatorはキーなし・networkなし、最終評価を候補生成へ開示しない |
| 稼働への反映 | 自動採用・ポジション変更・自動リリースなし |

実API呼出し、対象プロジェクトでのmodel利用、実請求照合、実市場での候補優位性は、この文書作成時点では未確認です。模擬usageを実支出と表示しません。Docker実隔離は、テスト名や構成ファイルだけでPASSにしません。

固定モデルの廃止予定、料金期限、保守的な予約計算は[正本のPhase 2節](ai-self-improvement/SOURCE_OF_TRUTH.md)を参照してください。新しい実AIの自動反復や定期実行は、単一候補フローの動作確認から自動的に追加されません。

## 6. 引き継いだ継続案件

| 案件 | 次に扱う内容 | 今回と分ける理由 |
|---|---|---|
| NEWS PR #2 | verified RSS、時刻・内容照合、SSRF、試行・費用、実ニュース互換性 | 通常Botはmock維持。コード採用とlive有効化を分ける |
| FAST_START PR #3 | proxyデータの表示、独立口座、比較の妥当性 | 本体の足・口座を置換しない |
| BACKFILL | 公開約定履歴の完全性、欠損証明、再接続との結合 | 未追跡002 migrationをAI追加のために適用しない |
| 独立dashboard | 保存観測値とリアルタイムの区別、停止中環境の検証 | 別ローカルリポジトリ。今回のフロントを上書きしない |
| read-only Gateway | 認証付き上流要求のredirect拒否と文書の一致 | 将来の外部HTTPS経路有効化前に検証 |
| バックアップ・監視 | 実際の周期、容量、保持、別DB復元、RPO/RTO実測 | 運用済みと提案を区別。新しい有料契約を前提にしない |

## 7. 正本と過去記録

[AI正本](ai-self-improvement/SOURCE_OF_TRUTH.md)、[計画書](ai-self-improvement/PLAN.md)、[技術書](ai-self-improvement/TECHNICAL_DESIGN.md)はv0.2へ整合します。研究数値は`ai_lab/policy.json`、API料金・予約は`ai_lab/phase2_policy.py`、実施結果は日付付き検収書を参照します。

過去のsecurity検収書には「PR未merge」「VPS未配備」「復元未検証」「同一HDD保管不可」等の当時の記述があります。日時付き履歴を消さず、後続の証跡とユーザー指定を本入口から区別します。外付けHDDへのバックアップと鍵の同居は独立災害対策ではありませんが、別媒体購入・有料vault契約を今回のmerge条件へ追加しません。

## 8. mergeとリリースの受入

11:11 JSTのGitHub再確認では、mainは必須`security`チェック、strict、管理者にも保護適用、承認1名、古い承認の破棄、最新pushへの承認、会話解決を要求しています。

実装・隔離検証・秘密検査・文書整合・PR準備を完成させ、最新HEADに対する必須CIと独立承認が揃った時点でmergeします。今回のユーザーのmerge希望や過去PRの承認を、新しいPRのGitHub承認の代わりにしません。保護解除・自己承認・管理者bypassは行いません。

merge後はmainのSHAとCIを検収書へ記録します。mergeだけで稼働環境とDBは変わらないため、不要なコンテナ再起動やDB migrationを実施しません。戻すときはrevert PRを使い、履歴・研究台帳・残高・volumeを削除しません。
