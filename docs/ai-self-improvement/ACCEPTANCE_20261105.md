# AI自己改善エンジン MVP 検収書

検証日：2026-11-05（日本時間）。対象は [正本 v0.1](SOURCE_OF_TRUTH.md) のオフライン研究MVPです。

## 1. 実施範囲と結論

| 区分 | 状態 |
|---|---|
| 計画書・技術書・正本・運用手順 | 作成済み |
| 候補生成・制約検査・評価・選抜・台帳・結果HTML | コード改修済み |
| 固定応答・合成データ・Pythonでの隔離検証 | 検証済み。96テスト成功、50,000円／13,000円の一連の実行成功 |
| iMacの独立したDocker projectでの実行 | 検証済み。最終ソースで6試行、正常終了、実コンテナの隔離設定を確認 |
| 結果HTMLの実ブラウザ表示 | 1440px・500pxのChrome表示を確認。iPhone実機は未確認 |
| GitHub改修ブランチ | `feat/ai-self-improvement-mvp-20261105` が提出先。提出SHAと公開結果はGitHubおよび最終報告を参照 |
| 稼働中Bot・既存残高・既存DB・既存volume | 未反映。研究から接続・更新していない |
| 実OpenAI API・実モデルの品質／Schema受理 | 未実施。契約を固定応答で検証した範囲 |
| Coincheck private API・実注文・有料ニュース | 未実施。実行経路を追加していない |
| 実相場での利益改善 | 未検証。合成データの利益を成績の証拠にしない |
| DigitalOcean / VPS | 未検証。作成・配備・設定変更をしていない |

「自己改善」は、許可した数値と条件の組み合わせについて研究候補を更新する範囲です。
MVPには任意Pythonの自己書換え、常駐の自律学習、稼働Botへの昇格・デプロイ機能はありません。
OpenAIモデルを呼ぶ実装と費用管理は、[計画書](PLAN.md) のPhase 2です。

## 2. 作業先と既存環境の確認

読み取り確認によって、次の状態を確認してから別worktreeを使用しました。過去の検収書だけを根拠にしていません。

| 対象 | 確認結果 |
|---|---|
| 接続先 | `iMac-2.local` |
| 元の作業場所 | `/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot/` |
| 元のHEAD | `9ab6bf79fb07342ae6026e332d0a2b72081d6cce`、未コミット変更なし |
| GitHub main / 今回の基点 | `a6a5f75b91b95fae0aa6c5fa25e348e0a2d5edbf` |
| 今回のworktree | `/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-ai-lab/` |
| 改修ブランチ | `feat/ai-self-improvement-mvp-20261105` |
| 通常のDocker context | `colima-coincheck-ai`。変更していない |
| 研究検証のDocker context | `colima-coincheck-sec-test` をコマンド単位で指定 |
| 研究用Compose project | `coincheck-ai-lab-mvp` |
| 研究用台帳 | 研究専用SQLite。既存PostgreSQLへの接続なし |

元のBotとGitHub mainのSHAは一致していません。今回の追加を理由に元のBotを更新していません。
元の5コンテナは2026-11-04 03:04 UTC台の起動を維持し、frontendは `127.0.0.1:8097`、APIとDBにはホスト向けポート割当てがありません。
既存のセキュリティ検証用5コンテナも別projectとして存続しています。
最終照合の結果は、元のworktreeのHEADとclean状態、既存10コンテナのID・起動時刻・稼働状態・ポート、default contextが変わっていないことを確認しました。

以下はこの改修ブランチで差分がないことを検査しました。

```bash
git diff a6a5f75 -- app compose.yaml schema.sql migrations frontend
```

既存の履歴データや残高は稼働Botが通常どおり更新し得るため、作業前後で値が同一とは主張しません。
確認したのは、研究の追加からそれらへ接続・初期化・上書きする処理を行っていないことです。

## 3. テスト結果

最終コードのPython標準ライブラリテスト：

```bash
python3 -m unittest discover -s tests -q
```

```text
Ran 96 tests in 6.061s
OK
```

実行環境は隔離した作業コピーのPython 3.12.14です。既存32件と今回追加64件を含みます。
この32件の再実行は、旧セキュリティ改修のPostgreSQL・SSH・VPS統合検証を今回すべてやり直したという意味ではありません。

| 検証領域 | 方法と結果 |
|---|---|
| 固定比較戦略 | 2h EMA21/55、RSI9・55上抜け・Window3、確定4h EMA50/200、ATR14・SL3/TP12を維持。直近450本の同じ窓を既存 `app.strategy.evaluate` へ渡し、判定・ATR・丸め後の各指標が一致 |
| 指標の因果性 | 将来部分を改変しても過去のシグナルは不変。上位足は確定済みだけ。遅延した利用可能時刻の入力では新規シグナルを抑止 |
| 入力 | UTC、5分境界、OHLC、重複、欠損、NaN、Infinity、未確定足、混在source、不正CSVヘッダーを拒否 |
| 起動条件 | DRY_RUN解除、LIVE有効化、mock以外、Coincheck秘密、OpenAI秘密、既存DB接続設定を研究CLIが拒否 |
| 候補の権限 | 未知フィールド・コード・URL・予算変更・不正型・範囲外を拒否。名前変更・条件順変更でも同じ戦略hash |
| 購入上限 | 50,000円／13,000円を検査。別の小型fixtureで手数料10bpsを与え、代金＋手数料が上限以下であることを確認 |
| 残高と注文制約 | 利用可能JPYが予算以下なら数量を減額。最低数量を満たせなければ拒否。通常売却の最低金額未達は保有を継続 |
| 決済 | 購入時ATR・SL/TP固定、次5分足始値、同一足SL/TPはSL優先、ギャップ損切り、同一RSIクロスの再購入防止 |
| 期間末 | 評価用清算と通常決済件数・実現損益を分離。最大下落率は5分終値ベースで、足内の最大逆行ではないことを明示 |
| 研究の制御 | 生成前の予約を別SQLite接続から確認。監査失敗時のrollback、競合ロック、上限消費、重複・不正候補の消費、600秒監督停止の伝播を検査 |
| 再開 | 予約だけの試行を再生成しない。保存済み候補は同じ候補で再開。完了済みは追加生成・評価なし。manifest変更は拒否 |
| 最終評価 | 開発3区間の時系列・非重複・被覆を確認。最良1候補だけ、評価前予約、重複期間拒否、中断しても消費、次点投入なし |
| OpenAI契約 | 実HTTPなしのfixture/spy。正常な構造化応答、拒否・不完全・複数出力・tool呼出し・重複キー・未知キーを検査。非fixture transportは拒否 |
| 結果画面 | HTML escape、CSP、合成表示、実効予算・初期資金をmanifestから参照。初期資金の表示用ハードコードを除去 |

最終評価の合格・不合格・中断経路は固定metricの制御テストで確認しています。
次節の合成価格の実行では全候補が開発段階で不採用となったため、最終区間の価格シミュレーションは実行していません。

## 4. 合成データでの一連の実行

使用したのはseed 47、180日、51,840行の**合成**5分OHLCVです。
データhash：`f27fb12925c839c66e5245eccb5703d4c18b8e84b449fda6db14270adb97fb33`。
50日の準備期間の後を、学習・開発・最終評価へ時間順に分けました。
初期資金は各評価100,000円です。

```bash
python3 -m ai_lab demo --workspace runtime/acceptance-ai-lab \
  --run-id acceptance-50000 --max-trials 6 --buy-budget-jpy 50000
python3 -m ai_lab demo --workspace runtime/acceptance-ai-lab \
  --run-id acceptance-13000 --max-trials 6 --buy-budget-jpy 13000
```

| 項目 | 50,000円設定 | 13,000円設定 |
|---|---:|---:|
| 候補試行数 | 6 | 6 |
| 全候補の開発・費用ストレス・3区間の購入支出 | 全て50,000円以下 | 全て13,000円以下 |
| 観測した最大購入支出 | 49,999.9984886759円 | 12,999.999566629413円 |
| 結論 | `NO_IMPROVEMENT` | `NO_IMPROVEMENT` |
| 最終区間の予約数 | 0 | 0 |
| 監査hash連鎖 | 整合 | 整合 |
| 全実行時間の実測 | 74.902秒 | 73.766秒 |
| 完了後の再実行 | 0.920秒・記録増加なし | 0.808秒・記録増加なし |
| 実OpenAI呼出し | 0 | 0 |

時間はこの作業コピーでの実測値で、他環境の性能やRTOを保証しません。
各候補の通常決済は3〜12件で、20件の条件を満たしていません。
利益が正の候補も、件数・利益集中・期間別安定性などの基準により不採用です。
基準を緩めて「成功した改善」を作らず、既存の戦略を維持する結果になりました。

この実行のソースhashは `a6aa20d11eb87d0afbade9145377c3de6eb435f7403ef82502e6d9b34637a5e3` です。
その後、結果画面の初期資金表示をmanifest参照に修正し、96件のテストを再実行しました。数値評価器とこの節の評価結果は変えていません。
最終ソースhashは `86750724ed4ac9e74b130eaf17320cafa0f22dd6034efc449bc46c33900c633a`、最終ポリシーhashは `e1dc75ad5084aa694c213f60423700d386c91f330e91ffbebd9853e46975a86b` です。
次節のDocker検証は最終ソースを使用しています。

## 5. Dockerと画面の実確認

最終ソースを、`DOCKER_CONTEXT=colima-coincheck-sec-test` と `DOCKER_BUILDKIT=0` を指定してbuildしました。

```bash
docker-compose -f compose.ai-lab.yaml -p coincheck-ai-lab-mvp build
docker-compose -f compose.ai-lab.yaml -p coincheck-ai-lab-mvp run --no-deps \
  --name coincheck-ai-lab-mvp-acceptance-13000 research \
  demo --workspace /var/lib/ai-lab/research \
  --run-id docker-acceptance-13000 --max-trials 6 --buy-budget-jpy 13000
```

コンテナのPythonは3.12.16。6候補すべて `REJECTED`、runは `NO_IMPROVEMENT`、OpenAI実呼出し0、監査連鎖整合、終了コード0でした。
Compose起動から検査までの実測は140.087秒です。
使用イメージID：`sha256:0fcbdc58c2a89cc4a8c6439b659c446c95321203feae08f6f6ab7922dbed8922`。

`docker inspect` の必要項目で次を実確認しました。環境変数全体や秘密をログへ出していません。

| 項目 | 実確認 |
|---|---|
| ネットワーク | `none` |
| root filesystem | read-only |
| 実行ユーザー | `10001:10001` |
| Linux capabilities | `ALL` drop、privileged=false |
| 追加権限 | no-new-privileges有効 |
| リソース | PID 32、メモリ512MiB、CPU 0.5 |
| ホスト公開ポート | なし |
| 永続mount | `coincheck-ai-lab-mvp_research_data` → `/var/lib/ai-lab` のみ |
| 秘密・既存DB・Docker socket | mountなし、秘密環境変数なし |

終了済みの研究用コンテナと研究volumeは証跡として残しています。既存volumeの削除・`down -v` はしていません。

Dockerから取り出したHTMLを、既存Chromeプロファイルと別の一時プロファイルで開き、1440pxと500pxの画面を確認しました。
合成データ表示、購入上限13,000円、初期資金100,000円、自動反映無効、採否と不足理由が表示されます。
390px指定ではheadless Chromeの実viewportが500pxになることをプローブで確認したため、390pxやiPhone実機で検証済みとはしていません。
結果の[公開例](examples/README.md)はこの最終ソースのDocker実行から作成しています。

## 6. 公開差分・秘密・依存関係

既存Botの `.gitignore` によるruntime、データ、秘密、DB、バックアップの除外を維持します。
公開するファイルは研究コード、文書、テスト、独立ComposeとDockerfile、README/CI/ignoreの必要な差分です。
秘密、実DB、実残高、実取引履歴をpublicへ追加していません。

classic builderがDockerfile専用ignoreを読まない問題に対して、通常の `.dockerignore` に公開正本1ファイルだけの例外を設けました。
その他のdocs、秘密、台帳、入力データをbuild contextへ広げていません。
BuildKit用の `Dockerfile.ai-lab.dockerignore` は必要なファイルだけのallowlistです。

```bash
git diff --cached --check
python3 scripts/scan_secrets.py --history
```

ステージしたコード・文書・合成の公開例と到達可能なGit履歴を既存の値非表示scannerで検査し、`SECRET_SCAN_PASS inspected=153 history=True` を確認しました。
これは当該scannerのパターン検査であり、全種類の資格情報の検出を保証するものではありません。

依存関係の扱い：新規Python依存なし、既存Python base imageのdigestを固定、既存ActionsのSHA固定と最小workflow権限を維持しました。
CIにはオフライン研究イメージのbuildと、ネットワークなしの実行を追加しています。
提出commitに対するGitHub Actionsの実結果は、[Actions](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/actions) と提出時の最終報告で確認してください。
手元の96件とDocker検証を、GitHub上のCIが成功したことの代用にしません。

GitHub APIで現在のmain保護を読み取り確認しました。`security` check必須・strict=true、承認レビュー1件、管理者にも保護適用、force push不可、削除不可です。今回これらの設定は変更していません。

新しいイメージのOS全体の脆弱性スキャンは今回未実施です。digest固定やpipの検査を、OS脆弱性が存在しない証明として扱いません。

## 7. 残る制約と次の段階

- 固定応答の候補生成は、実OpenAIモデルの改善提案能力を証明しません。モデル選定、実APIでのSchema受理、費用予約・照合、日次制限は未実装・未検証です。
- 実市場のデータ取得・来歴検証・ペアの機械検証・収集時刻管理、実際の注文板・部分約定・通信遅延はMVPにありません。
- 5分OHLCVの損切り／利確順序は仮定です。価格が飛んだ場合の流動性や、期間末清算の実行可能性を保証しません。
- 8試行上限、3開発区間、一度だけの最終評価は過学習を完全には防ぎません。試行系列全体の累積履歴、統計的検証、未使用の将来データでの比較が必要です。
- 最終期間の使用記録は同じ台帳内の制御です。管理者による台帳破棄・置換を防ぐ暗号署名や外部監査サービスはありません。
- 時間制限は1回の研究評価部です。入力読込み・レポート・再開を含む累積の上限ではありません。保存量の検査はOSの厳密なdisk quotaではありません。
- 新しい常駐HTTP API・管理操作・自動paper昇格・任意コード実行は未実装です。結果は保存された研究内容を読む静的HTMLです。
- VPSのSSH、Firewall、外部到達性、再起動、バックアップ性能などは今回の検証対象外で、未確認です。

実データ検証・OpenAI実接続・限定paper反映・コード変更案の段階条件は[計画書](PLAN.md)、実行・再開・保全・戻し方は[運用手順](OPERATIONS.md)にまとめています。
稼働環境への反映は、このMVPの提出とは別に対象SHA・設定・ポジションの扱いを確認して実施します。
