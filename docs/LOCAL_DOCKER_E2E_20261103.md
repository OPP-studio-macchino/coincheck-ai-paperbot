# iMac Docker E2E 検収 — 2026-11-03 JST

対象: `coincheck-ai-paperbot`（公開コードのみ）。実発注機能なし、DRY_RUN専用。

## 検収結果
- GitHub公開: `OPP-studio-macchino/coincheck-ai-paperbot`、main初期コミット `32357f5`。GitHub Actions「Paper-only CI」PASS。
- インストール: Homebrew Docker CLI 29.9.2、docker-compose 5.6.1、Colima 0.10.3。専用プロファイル `coincheck-ai`。
- iMac内蔵SSD容量不足対策: Colimaホーム・30GiB仮想ディスクを外付け `/Volumes/OPP_ARCHIVE/.colima-coincheck` に置く。内蔵SSDの既存開発データは削除していない。
- 専用ColimaのUbuntu 24.04でDNSの壊れた `/etc/resolv.conf` を修復し、Docker RegistryからPostgreSQL、Nginx、Pythonのイメージを取得。**Mac本体・既存VPSのDNSは変更していない**。
- Colimaには `/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot` を書き込み可能にマウント。未マウントの初回試行では schema.sql がディレクトリ扱いになってDB初期化に失敗したため、適切なマウントへ修正。
- 作成済みPostgreSQLボリュームは**削除せず**、原本 `schema.sql` を `psql -v ON_ERROR_STOP=1` で適用。初期スキーマの適用が正常終了したことを確認。
- `docker-compose up -d --build` / `docker-compose up -d --force-recreate` で5サービス（db, api, market, news, frontend）を起動。
- DB・API: Healthy。frontend: Up、外部公開なしで `127.0.0.1:8097` にのみバインド。market/news: Up。
- Coincheck公開WebSocket: MARKET_CONNECTED。2026-11-03 07:49 JST時点でDBのmarket_ticks 35件、candles 2本（**現在足・未確定足を含む**）。
- ニュース: `NEWS_MODE=mock`、state `unknown`、OpenAI API実呼出なし。ニュース併用口座の新規買いは保留。
- ローカルHTTPSなしのGET/POSTスモーク: `GET /` 200、`GET /healthz` 200、`GET /api/status` 認証なし401／認証あり200、`GET /api/news, /api/fills, /api/decisions, /api/candles` すべて200。
- `POST /api/control/pause` は true/false 往復PASS、**元の値falseに復帰**。
- 仮想約定0、売買判断0（Coincheckの連続4時間足300本を待つ保守的なウォームアップ中）。実注文0。
- Python単体テスト18件PASS。GitHub CI PASS。トークン・DBパスワード等の値は本書に記載しない。

## 運用・未完了
- VPS本番デプロイ：未実施。接続先VPSや既存サービスには触れていない。
- 売買戦略のCoincheck実データでの長期再現：未完了。最低300本の確定4時間足と連続した2時間足が必要で、中断後は判断保留となる。
- OpenAI実ニュース分析：未実施。課金キーを入れずmockで運用。
- 監視・バックアップの運用定着：初期スクリプトあり、VPS上での復元試験未実施。
- 外付けHDDを取り外した場合、Colima仮想マシンを停止してから行うこと。iMacスリープ・再起動・通信切断時にリアルタイム収集が止まり得る。VPSへ移行するまで常時運用は保証しない。
- DockerやPostgreSQLボリュームを消す `docker-compose down -v` は**禁止**。

## 再確認コマンド

```bash
cd "/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot"
docker-compose ps
docker-compose logs --tail=20 market news api
python3 -m unittest discover -s tests -q
open http://127.0.0.1:8097
```

画面ログイン用の `DASHBOARD_TOKEN` はiMacのローカル `.env` にのみ存在する。**公開GitHubには掲載しないこと**。
