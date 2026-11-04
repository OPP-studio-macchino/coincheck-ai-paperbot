# PostgreSQLバックアップ・暗号化・復元・保持

**安全事項：`docker compose down -v`, `DROP DATABASE paperbot`, 既存walletの初期化は禁止。** VPS配備前にはこの手順を別DBで実機検証すること。

## 1. 分離すべきもの

- バックアップの**暗号化公開鍵**（age recipient）はVPSに置いてよい。`secrets/backup_recipient.pub`に保存する。これは公開鍵のみで復元できない。
- **復号用のage private identity**はVPSに常駐させず、オフラインの別管理端末／オーナー管理の安全な場所に置く。GitHub、.env、Dockerイメージ、VPSバックアップと同じ権限領域に置かない。
- 暗号化されたdumpはVPS内`backups/`への一時保存後、**VPS外**のアクセス制限付き保管先へ転送。復号鍵の管理者とバックアップの削除権限を分ける。外付けHDDは検証用保管候補であり、それだけで災害対策完了とは扱わない。
- PostgreSQLの`pg_dump -Fc`は1時点で整合したスナップショットを取得できるが、WALベースの任意時点復旧ではない。バックアップ間の約定履歴は失われ得る。**RPO/RTOは保存周期・遠隔コピー・実復元時間を測ってから**決める。

## 2. 暗号化バックアップ生成

Linux VPSで`age`（オープンソースCLI）とDocker Composeを導入し、公開鍵を確認する。復号鍵をVPSへコピーしない。

```bash
cd /srv/coincheck-ai-paperbot  # 将来の配置先の一例。今回は未作成
# secrets/backup_recipient.pub には age1... の公開鍵だけを入れる
bash scripts/backup.sh
# backups/paperbot_YYYYMMDDTHHMMSSZ.dump.age と .sha256 を生成
```

スクリプトは`pg_dump -Fc`の標準出力を`age -r`へパイプし、**平文dumpファイルを作らない**。一時`.partial`から同一フォルダでrenameし、サイズ検査とSHA256記録。バックアップは`.gitignore/.dockerignore`の対象で公開しない。

実際のDB容量・バックアップ所要時間・遠隔保存コストを計測して、実測できた周期で自動化する。ローカルMacがオフラインになると取得できない方式は本番定期バックアップとして不適切。バックアップの成功検査・保管先の空き・最終更新・復元試験も監視する。

## 3. 復元は必ず別DB

`scripts/restore_clone.sh`は`paperbot_restore_...`という新しいデータベース名のみ受け付け、同名DBが存在すれば`createdb`が失敗して停止する。本番DBと同名では復元できない。

```bash
# 復号鍵が存在する認証済み端末でのみ。GitHub/VPS上に鍵を保存しない。
export BACKUP_IDENTITY_FILE=/PRIVATE/OFFLINE/PATH/age-identity
# SSHで安全に接続したDocker context等が必要。実際の環境に合わせて検証。
bash scripts/restore_clone.sh \
  backups/paperbot_YYYYMMDDTHHMMSSZ.dump.age \
  paperbot_restore_drill_01
```

この復元用プログラムはSHA256を先に検査し、ageで復号した標準出力を`pg_restore`へ直接渡す。コマンドライン引数へprivate keyの**中身**を入力しない。復元後は`portfolios`の2口座、BTC/JPY約定件数、決済／監査履歴、schema migration記録を読み取り照合する。失敗時に本番DBへフォールバックする機能はない。cloneの検証用DBでも無断削除しない。

## 4. 2026-11-03実測

- **隔離DBの暗号化バックアップ生成：PASS**。 `age`によるファイル生成、`.age`とSHA256、600権限を確認。
- **稼働main DBの読み取り専用`pg_dump`→別のPostgreSQLへパイプ復元：PASS**。既存DBに変更なし。復元先は`paperbot_restore_20261103`で、portfolio2件と収集tick件数を確認。
- **コピーした元DBへのversioned migration：PASS**。portfolio行のMD5は適用前後で一致。`rollback-permissions`と再適用もPASSし、MD5不変。
- **age private keyを使用した`restore_clone.sh`の復号・復元までの一貫試験：未完了**。セキュリティツールの承認が得られず試験用private keyの保管場所を分離できなかったため、保護機構を迂回しなかった。このため暗号化復元の完遂とは主張しない。
- **VPS外への転送、異なる障害ドメインへの保存、削除権限の分離、RPO/RTOの実測：未実施**。DigitalOceanは未作成。

## 5. 保存・削除権限と障害対応

バックアップは保存時刻・SHA256・バックアップ対象commit・schema migration版・件数などの**秘密を含まない台帳**で管理する。復号鍵の保管先は公開台帳に書かない。保管先削除権限を取得用資格情報から分離し、誤削除とランサムウェアに備えた世代管理を検討する。

データ異常では新規購入停止→証跡保全→バックアップ取得→別DBで検証の順とする。異常検知のみで実データを削除・再初期化しない。市場収集が止まっている期間に補完できないローソク足を作らない。
