# AI自己改善エンジン 実行・運用手順

対象版：`ai-self-improvement-v0.2`（2026-11-06 JST）。[正本](SOURCE_OF_TRUTH.md)の権限を維持します。Python 3.12を標準とし、研究CLIは標準ライブラリで動きます。Bot全体の検証依存は`requirements.lock`を参照してください。

この手順は研究用です。コードmerge、既存Botへのリリース、既存DB migration、VPS配備、API live有効化は別々に管理します。実行済みの範囲は[Phase 2検収書](ACCEPTANCE_PHASE2_20261106.md)で確認します。

## 1. 作業場所と状態を確認する

統合ブランチは`feat/ai-integration-20261106`です。管理者が確認した統合用worktreeで実行してください。元のBot、security、AIの作業場所をcheckoutし直したり、未コミット変更を破棄したりしません。

```bash
git status --short --branch
git rev-parse HEAD
python3 --version
```

2026-11-06の開始確認では、iMacの既存Colima profilesは停止していました。旧手順のcontext名が存在することを稼働証拠とせず、検証専用環境またはCIを選びます。稼働Botの`.env`をsourceせず、秘密をコピーしません。

以下のローカル例は、作業ディレクトリを明示し、出力をGit除外対象の`runtime/`へ置きます。

```bash
umask 077
COINCHECK_LAB_ROOT="$(pwd -P)"
COINCHECK_LAB_INPUT="$COINCHECK_LAB_ROOT/runtime/ai-phase2-input"
COINCHECK_PHASE2_STATE="$COINCHECK_LAB_ROOT/runtime/ai_phase2_state"
mkdir -p "$COINCHECK_LAB_INPUT"
```

`runtime/`以下の実データ、台帳、成果物、秘密をpublicへ追加しません。既存のstateへ上書きする目的でこの手順を使わないでください。

## 2. Phase 0のオフラインデモ

```bash
python3 -B -m unittest discover -s tests -q
python3 -m ai_lab demo \
  --workspace runtime/ai_lab \
  --run-id demo-50k \
  --max-trials 6 \
  --buy-budget-jpy 50000
```

macOSの`/var`がsymlinkである環境では、テストの一時領域に実体のある場所を指定します。

```bash
TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s tests -q
```

この回避はテスト一時ディレクトリの指定です。実運用のsymlink拒否を弱めません。

デモは180日分の合成5分OHLCVを生成し、50日の指標準備後にtrain/dev/holdoutへ分割します。研究初期資金は100,000円、1回の手数料込み購入上限は50,000円です。実口座残高を初期化する処理ではありません。

| 保存物 | 内容 |
|---|---|
| `research.sqlite3` | 試行・候補・最終期間使用・監査 |
| `inputs/synthetic-180d-seed47.csv` | 合成入力 |
| `demo-50k.json` / `demo-50k.html` | 保存済み研究結果 |

Macでは`open runtime/ai_lab/demo-50k.html`で結果を開けます。HTTPサーバや管理トークンは不要です。保存時点の研究結果を、現在の口座や相場として扱いません。

13,000円の比較は同じ研究台帳の別run IDで実施できます。

```bash
python3 -m ai_lab demo --workspace runtime/ai_lab \
  --run-id demo-13k --max-trials 6 --buy-budget-jpy 13000
```

同じ最終期間が既に消費されている場合、後の実験で使い直せません。都合のよい結果を得るため台帳を作り直さないでください。

## 3. 凍結CSVの検査

```csv
timestamp,open,high,low,close,volume,source,available_at
2024-01-01T00:00:00+00:00,6000000,6010000,5990000,6005000,1.25,coincheck,2024-01-01T00:05:00+00:00
```

必須列は先頭6列です。この1行は書式例で、研究に必要な期間を満たしません。

- UTCの足開始時刻、正確な5分間隔、正のOHLC、非負の出来高を要求します。
- `source`は`synthetic`、`bitbank`、`coincheck`、`unknown`。混在は拒否します。
- `available_at`は利用可能時刻です。省略時は足終了時刻を仮定し、実収集時刻の証明とはしません。
- 欠損、重複、未来足、無効値を修正・補間して正常扱いにしません。
- hashとsource文字列だけで提供元の真正性を証明しません。BTC/JPYであることとデータの出所は利用者が確認します。
- 別取引所履歴をCoincheckの実約定履歴とは表示しません。ニュース効果の検証には別途、当時利用できた記事履歴が必要です。

```bash
python3 -m ai_lab validate-data \
  --csv /absolute/path/to/frozen_btcjpy_5m.csv \
  --as-of 2026-11-06T00:00:00Z
```

`--as-of`は実際の確認時点へ置き換えます。未来の時刻は使えません。省略時は実行時刻で検査します。固定応答探索を実データで行う入口は次です。

```bash
python3 -m ai_lab run --workspace runtime/ai_lab --run-id research-001 \
  --csv /absolute/path/to/frozen_btcjpy_5m.csv \
  --max-trials 8 --buy-budget-jpy 50000
```

## 4. Phase 2の全経路をfixtureで確認する

ここではAPIキーも外部HTTPも使いません。fixtureのmodel、usage、費用換算は模擬値で、実AI生成や実課金ではありません。

### 4.1 要求書を作る

2節の合成入力を使う例です。入力CSVのパス、予算、コードを、その後の評価でも同じにします。

```bash
COINCHECK_FROZEN_CSV="$COINCHECK_LAB_ROOT/runtime/ai_lab/inputs/synthetic-180d-seed47.csv"
COINCHECK_REQUEST_FILE="$COINCHECK_LAB_INPUT/request.json"
python3 -m ai_lab prepare-proposal \
  --csv "$COINCHECK_FROZEN_CSV" \
  --output "$COINCHECK_REQUEST_FILE" \
  --buy-budget-jpy 13000
```

要求書には凍結データ・分割・policy・コード・prompt・schema・予算が結び付きます。modelへ渡すのはtrain統計と許可済みcontextだけです。trial 0 / feedback空の単一候補フローであり、実AIの多段自動探索はありません。

### 4.2 模擬予算台帳を明示初期化する

以下は1回・日次・累積各3 USD、日次・累積各1回の**設定例**です。これを文書に載せたことは実費の使用承認ではありません。fixtureでは同じ上限制御を無料で試します。

```bash
python3 - "$COINCHECK_LAB_INPUT/limits.json" <<'LIMITS'
import json, os, sys
limits = {
    "per_call_limit_microusd": 3000000,
    "daily_limit_microusd": 3000000,
    "total_limit_microusd": 3000000,
    "daily_call_limit": 1,
    "total_call_limit": 1,
}
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    json.dump(limits, out)
    out.write("\n")
LIMITS

python3 -m ai_lab.phase2 init \
  --state "$COINCHECK_PHASE2_STATE" \
  --limits "$COINCHECK_LAB_INPUT/limits.json" --mode fixture
```

同名limits fileや初期化済みstateを作り直す操作ではありません。初期化後の上限・料金版は固定されます。別ファイルを指定して上限を増やす機能、reset、top-upはありません。

### 4.3 候補生成とオフライン評価

```bash
python3 -m ai_lab.phase2 generate \
  --state "$COINCHECK_PHASE2_STATE" \
  --context "$COINCHECK_REQUEST_FILE" --mode fixture

python3 -m ai_lab.phase2 status \
  --state "$COINCHECK_PHASE2_STATE" --mode fixture
```

`generate`が返す`result.artifact_path`を確認します。要求書から検証済みhashを読むと、同じ保存先を組み立てられます。

```bash
COINCHECK_REQUEST_HASH="$(python3 -c 'from pathlib import Path; import sys; from ai_lab.phase2_artifacts import read_envelope; print(read_envelope(Path(sys.argv[1]))["binding"]["request_hash"])' "$COINCHECK_REQUEST_FILE")"
COINCHECK_ARTIFACT_FILE="$COINCHECK_PHASE2_STATE/fixture/artifacts/$COINCHECK_REQUEST_HASH.json"
python3 -m ai_lab evaluate-artifact \
  --csv "$COINCHECK_FROZEN_CSV" \
  --artifact "$COINCHECK_ARTIFACT_FILE" \
  --workspace "$COINCHECK_LAB_ROOT/runtime/ai_artifact_evaluation" \
  --run-id ai-one-001 --buy-budget-jpy 13000
```

評価試行は1固定です。`--artifact`へ台帳やキーのファイルを渡しません。CSV、予算、policy、コード等が要求書と違えば、評価を始めず拒否します。

全候補不採用、標本不足、`NO_IMPROVEMENT`は正常な研究結果です。基準を下げたり同じholdoutへ候補を入れ直したりしません。

## 5. 実APIを有効化する場合の手順

**今回の無料検証とは別の有効化手順です。** この版をmergeしただけでは実APIを呼びません。対象コードSHA、専用キー、用途、有限上限、期限内料金版が揃っていることを確認します。既存のニュースキーや他プロジェクトのキーを流用しません。キーの値をチャット、コマンド引数、ログ、Gitへ載せません。

live用の台帳を別領域に初期化します。fixtureの予約や成果物を実AIの証跡として再利用しません。

```bash
python3 -m ai_lab.phase2 init \
  --state "$COINCHECK_PHASE2_STATE" \
  --limits "$COINCHECK_LAB_INPUT/limits.json" --mode live
```

管理者が安全に配置した専用キーfileを、生成コマンドだけへ指定します。ホストでは所有者限定のfileとprivateな親ディレクトリを使い、キー自体を環境変数へ直接設定しません。

```bash
AI_LAB_PHASE2_LIVE=true \
DRY_RUN=true LIVE_TRADING_ENABLED=false NEWS_MODE=mock \
OPENAI_API_KEY_FILE=/absolute/private/path/openai_phase2_key \
  python3 -m ai_lab.phase2 generate \
    --state "$COINCHECK_PHASE2_STATE" \
    --context "$COINCHECK_REQUEST_FILE" --mode live
```

この呼出しは最大1回でretryなしです。失敗・timeoutも事前予約を消費します。キーを評価器のシェル環境へexportしないでください。`status`はlive台帳の安全なmetadataを読む操作で、APIへ問い合わせません。

固定モデル`gpt-4.1-2025-04-14`は2027-01-14停止予定です。料金版は2026-12-06 00:00 UTCに先に失効します。新しいmodelや単価への自動切替はなく、履歴を保持するレビュー済み更新が必要です。現在の公式仕様と対象プロジェクトの利用可否は、有効化時にも確認します。ネットワークのないfixtureは期限後も試験できますが、liveの期限検査を迂回するものではありません。[モデル仕様](https://developers.openai.com/api/docs/models/gpt-4.1) / [廃止予定](https://developers.openai.com/api/docs/deprecations)

実応答が得られた後も、schema受理・model・usage・候補保存・評価までを対象SHAとともに検収します。receiptの`actual_microusd`はusageの通常単価換算で、実請求の照合ではありません。

## 6. Dockerで分離して実行する

3つのComposeはすべてstandaloneです。既存Bot用Composeと重ねず、phase2 fixture/liveファイル同士も`-f`で重ねません。Docker engineとprojectを先に照合し、稼働中のBotや既存の試験volumeを対象にしません。

| ファイル | 既定動作と権限 |
|---|---|
| `compose.ai-lab.yaml` | オフライン評価、networkなし、秘密なし |
| `compose.ai-lab.phase2.yaml` | fixture generator、networkなし、秘密なし、既定status |
| `compose.ai-lab.phase2.live.yaml` | 明示live profile、専用egress、generatorだけに専用キー |

まず、元のprivate入力を保持したまま、container UIDで読める非秘密のコピーだけを用意します。privateな外側ディレクトリはホストの他利用者による横断を防ぎ、innerだけをread-only mountします。すでに存在する保存先へ上書きするコマンドではありません。

```bash
COINCHECK_CONTAINER_STAGE="$COINCHECK_LAB_ROOT/runtime/ai-phase2-container-stage"
mkdir -m 700 "$COINCHECK_CONTAINER_STAGE"
mkdir -m 755 "$COINCHECK_CONTAINER_STAGE/input"
install -m 444 "$COINCHECK_REQUEST_FILE" "$COINCHECK_CONTAINER_STAGE/input/request.json"
install -m 444 "$COINCHECK_LAB_INPUT/limits.json" "$COINCHECK_CONTAINER_STAGE/input/limits.json"
COINCHECK_CONTAINER_INPUT="$COINCHECK_CONTAINER_STAGE/input"
```

このコピー領域へキー、口座情報、予算DBを置きません。元要求書・state・キーの権限を緩めないでください。

以下は新しい検証projectの例です。`docker compose` pluginがないiMacでは`docker-compose`へ読み替えます。停止中の既存Colimaをこの手順だけのために無確認で起動しません。

```bash
AI_LAB_INPUT_DIR="$COINCHECK_CONTAINER_INPUT" \
  docker compose -f compose.ai-lab.phase2.yaml \
  -p coincheck-ai-phase2-isolated config --quiet

AI_LAB_INPUT_DIR="$COINCHECK_CONTAINER_INPUT" \
  docker compose -f compose.ai-lab.phase2.yaml \
  -p coincheck-ai-phase2-isolated build

AI_LAB_INPUT_DIR="$COINCHECK_CONTAINER_INPUT" \
  docker compose -f compose.ai-lab.phase2.yaml \
  -p coincheck-ai-phase2-isolated run --rm --no-deps generator \
  init --state /var/lib/ai-generator --limits /input/limits.json --mode fixture

AI_LAB_INPUT_DIR="$COINCHECK_CONTAINER_INPUT" \
  docker compose -f compose.ai-lab.phase2.yaml \
  -p coincheck-ai-phase2-isolated run --no-deps \
  --name coincheck-ai-phase2-fixture-export generator \
  generate --state /var/lib/ai-generator --context /input/request.json --mode fixture
```

`AI_LAB_INPUT_DIR`には上記の要求書とlimitsのコピーだけを置き、`/input:ro`へ渡します。container UID10002による読取りを確認してから進めます。キーを入れたディレクトリや予算DBを入力mountに使いません。

生成コンテナは成果物を取り出せるよう`--rm`を付けていません。出力された要求hashで対象ファイルを特定し、停止済みのこのコンテナだけからコピーします。

```bash
docker cp \
  "coincheck-ai-phase2-fixture-export:/var/lib/ai-generator/fixture/artifacts/$COINCHECK_REQUEST_HASH.json" \
  "$COINCHECK_LAB_INPUT/candidate.json"
```

4節で求めた要求hashと生成コマンドの出力を照合し、コピー後に権限・所有者・hashを確認します。生成側のstateやキーを評価器へmountしません。評価側は成果物だけをread-only入力として受け取り、`network_mode:none`で実行します。停止済みコンテナやvolumeを調査せず削除する必要はありません。

live版は別のstandaloneファイルで、明示profileと5節の条件を必要とします。Compose secretsを設定しただけで正しいUID・mode・read-only mountが成立したとみなさず、実キーの内容を表示しない方法で確認します。通常のbridge networkはOpenAIだけを許可するFirewallではありません。Dockerによる実隔離試験と、socketを禁止したunit試験は別の証拠です。

## 7. 再実行・停止・障害対応

```bash
python3 -m ai_lab status --workspace runtime/ai_lab --run-id demo-50k
```

研究statusは保存済み結果からJSONとHTMLを再生成します。市場受信や候補生成は行いません。完了runを同じmanifestで再実行すると結果を再利用します。変更がある場合、旧結果を上書きしません。

| 状態 | 対応 |
|---|---|
| manifest・要求書の不一致 | 変わったデータ、コード、予算、policyを確認。旧実験を上書きしない |
| holdout使用済み | 未使用の将来データを待つ。履歴を削除しない |
| 研究が実行中 | 対象プロセスを確認。ロックを無条件削除しない |
| generatorに同requestの未確定/失敗予約 | API再送を止め、予約を保持。別stateへ逃げない |
| generator完了・成果物なし | 送信済みの可能性を前提に証跡を保全。再生成で埋めない |
| `PHASE2_*_LIMIT` / `PHASE2_PRICE_EXPIRED` | 新規live送信を停止。継続には履歴を保持する正式な変更が必要 |
| `PHASE2_CLOCK_ROLLBACK` | 時刻同期と最後の台帳時刻を確認。台帳を過去へ戻さない |
| `PHASE2_BUDGET_POLICY_MISMATCH` / `PHASE2_AUDIT_INVALID` | stateとコードを保全し、履歴を保持する手順で調査 |
| model/usage不整合 | 成果物を採用せず停止。生の応答や秘密を公開ログへ出さない |
| ディスク・SQLite・監査の異常 | 研究を停止し、別コピーで調査。DB初期化やvolume削除をしない |
| 全候補不採用 | 正常な結論。採用基準を後から緩めない |

Ctrl-Cで研究を止められます。既存Botの新規購入停止・決済とは独立です。Bot管理APIへの停止命令や全売却へ結び付けません。

研究評価処理の600秒上限は、入力検査・manifest確認後の評価に適用します。CSV取込・レポート・手動再開を含む累積wall timeやRTOの保証ではありません。生成HTTPの制限はgatewayの設定に従い、retryは0です。

## 8. 予算・データ・鍵の保全

研究と生成を止めてから、各専用ディレクトリ、入力、成果物、固定SHAを保全します。実行中SQLiteは単純コピーせずSQLite backup API等の整合した方法を使います。生成費用台帳と研究holdout履歴は、再起動後にも必要です。

生成予算は一つの永続stateで運用する範囲の制御です。別volume・他プロセス・他用途のキー利用を含むOpenAIアカウント全体の請求上限ではありません。所有者が台帳を変更・削除できることを前提に運用記録を残します。

既存Botの暗号化PostgreSQLバックアップは[専用手順](../security/BACKUP_RESTORE.md)を参照します。今回のコードmergeで稼働DBのdumpや復元を自動実行しません。ユーザー指定の外付けHDD保管は保持し、鍵とデータが同じ障害媒体にある限界を記録します。別媒体購入や有料サービス契約をこのmergeの新しい必須条件にしません。保存周期・復元を実測せずRPO/RTOを保証しません。

## 9. 設定・コード更新・merge・戻し方

- 研究予算やデータを変える場合は要求書と実験を作り直し、旧manifestは保持する。
- 候補範囲、採用基準、料金版、modelの変更はコード差分・テスト・正本の更新を通す。
- API費用の既存台帳に対する上限変更・料金移行は、履歴を保持する手順を別途設計する。台帳削除で代用しない。
- 対象ファイルだけstageし、秘密・runtime・実データを含めずcommit/pushする。
- 最新pushで必須`security` CI、正規の独立承認、会話解決を満たしてmergeする。保護解除・自己承認・管理者bypassは使わない。
- merge後も、通常Botのイメージ、稼働設定、既存DB、VPSは自動更新しない。

コードを戻す場合は保護ルールを満たすrevert PRを使います。mainをforce pushせず、研究台帳や仮想残高を削除しません。旧研究結果を確認する場合は旧SHAの別worktreeで読み、旧コードに新しい台帳を更新させません。

将来のBotリリース・DB互換性・ロールバックは[固定リリース手順](../security/CONFIG_RELEASE_ROLLBACK.md)で別に検収します。AI候補の採用も別の変更で、新規ポジションだけに新しい戦略版を適用し、既存の固定SL/TPを維持します。
