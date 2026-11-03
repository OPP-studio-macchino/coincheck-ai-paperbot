# 監視条件・インシデント対応 / Paper-only

**初期版で実装済みの情報を用いた監視提案。** DigitalOceanの監視・通知・Cron設定は未実施。数値は運用仮説であり、RPO/RTOや注文処理保証ではない。

## HealthとTrading Readyを分ける

`GET /healthz`が200でも、**DBへの照会ができるだけ**であり市場・ニュース・指標・仮想決済がReadyとは限らない。認証後の`GET /api/status`を見て判断する。

| 項目 | 閾値/表示案 | 異常時動作 |
|---|---|---|
| DB・コンテナ | db/api healthy、market/news/frontend up | 新規買い停止・証跡保持 |
| 公開約定の最終受信 | 120秒以上未更新→`market_fresh=false` | 価格評価不能・新規買い抑止 |
| 決済評価 | 120秒以上未評価・quote不可→`exit_evaluation.available=false` | 「決済監視中」と表示しない |
| 確定した2時間足 | 連続200本以上、最新の確定足を照合 | 不足件数と理由表示。未確定を数えない |
| 確定4時間足 | 連続300本以上、参照は直前確定足 | 未達/欠損時に新規買い停止 |
| ニュース | mock→unknown、期限切れ/出典不足/失敗→unknown | `news_gate`の新規買い停止。baselineは独立 |
| DB容量・ディスク | 空き20%未満で警報案（容量に応じ再評価） | 不要なログ/検証データだけを安全に見直す。volume削除禁止 |
| ログ | 繰り返し例外・ローテーション未設定の増大を警報 | 秘密値を出さず分類と件数で記録 |
| バックアップ | 最終成功時刻・暗号化・遠隔転送・月次別DB復元確認 | 未成功なら復元可能扱いしない |

価格はCoincheck取引所の公開約定から取得。新鮮なときのみ`cash + btc_qty * last_trade_price`で概算時価と含み損益を計算し、売却手数料・滑りはここでは**未控除**。価格時刻を表示し、古い価格を現在値と呼ばない。実口座APIは未接続。

## 異常検知時の手順

1. まず管理者の認証された`POST /api/control/pause`で**新規仮想購入だけ**停止。保有中のATR決済評価は可能な限り継続するが、marketやAPI/DB停止の場合、評価ができないことを正確に表示する。
2. ブラウザ/SSHから独立して稼働するDockerの状態、公開受信時刻、確定足数、決済評価、ニュース期限、Git SHA・設定版を読む。未確認と正常を混同しない。
3. 監査証跡（`admin_audit`、`decisions`、`paper_fills`、`feed_sessions`、`errors`）、コンテナログ、公開済みGit commit、時刻、ホスト・ネットワーク設定、DBバックアップを保全。API秘密値やtokenをログ・チャットに転記しない。
4. 攻撃・漏洩が疑われる場合は影響範囲を特定してCloud Firewallと鍵権限で**対象だけ**隔離する。PostgreSQLの永続領域を削除しない。SSH接続の回復経路を残す。公開アクセスできないlocalhostポートは勝手に`0.0.0.0`へ広げない。
5. 侵害された管理/viewトークンやDBパスワードは失効・再発行。Git、イメージ層、CIログ、バックアップにも漏れた可能性を調べる。OpenAI/取引所秘密鍵は今回使わないので、存在した場合は設計違反として対処する。
6. cleanイメージ・固定リリース・検証済みクローンDBで復旧。復元の成功とデータ件数・ポジション・固定SL/TPが一致することを確認し、承認後に新規買い再開。障害検知を条件に自動全売却は行わない。

## 検収用コマンド（対象Compose projectを確認してから）

```bash
docker compose ps
docker compose logs --no-color --tail=50 market news api db frontend
docker compose config --format json
ss -ltnp
df -h
date -u
timedatectl status
```

認証APIへトークンをURL/コマンド引数で渡すスクリプトは採用しない。管理フロント上でロールを選択して確認する。API本体には任意Python実行・Web shell・Docker操作機能を持たせない。

## 継続性の限界

Docker restart policyはプロセス再起動を助けるが、VPS停電・DNS障害・Coincheck WebSocket欠損・板取得失敗に対する無停止保証ではない。接続が切れた間の約定履歴を勝手に補間しない。再接続後に正常な過去データは保持しつつ、継続足の安全判定は現在セッション基準。HTTP200やVPSが起動中というだけでは注文処理をReadyと表示しない。

**今回の検証では本番監視・異常通知・リモートバックアップは作動していない。**
