# DigitalOcean導入・SSH管理・ネットワーク運用 Runbook

**適用前の安全境界**：2026-11-03時点、Dropletの作成・SSH接続・Cloud Firewall適用・VPSへの配備はいずれも**未実施**。下記は将来の作業手順であり、既存iMacや他のO.P.P Studio本番サーバーへ適用してはいけない。

## 運用の構成（無料SSHトンネルが標準）

- VPS上のDocker Composeは**フロントのみ**`127.0.0.1:8097:8080`、内部APIとPostgreSQLはホストに非公開。
- Macではブラウザを起動する前に、SSHローカル転送を張る。SSH通信内でHTTPの管理画面へ接続する。トンネルはBotのプロセスと無関係。
- `market` と `news` はDockerのrestart policyによって、SSH切断後もVPSで動作。Web操作とDocker起動・停止を結び付けない。
- DB用 `db_private` はComposeの`internal:true`。DBはDB専用ネットワークのみ、news/marketは別`outbound`、APIは`front_edge`との中継、frontendはDBに接続するネットワークを持たない。Nginxのlocalhostポート公開には`loopback_transport`ネットワークが必要だがホスト公開は127.0.0.1限定。

## 1. 事前準備とDroplet作成後の初動（今回は実行しない）

1. DigitalOcean管理画面から専用Dropletを作成するとき、OSはサポート中のUbuntu LTSを使用する。小さすぎるメモリやディスク、追加課金のバックアップ・VPN等は自動有効化しない。IPv4、IPv6、OSログイン方式と料金をオーナーが確認する。
2. macOS上で本人管理のED25519 SSH公開鍵を用意。秘密鍵・known_hostsをGitへ入れず、個別のSSHホスト鍵指紋をDigitalOceanコンソールなど**独立した経路**で照合する。既知ホスト鍵を安全確認なしで削除・無視しない。
3. 初期コンソールから一般管理ユーザー `bot-admin` を作成し、sudo権限と `~bot-admin/.ssh/authorized_keys` のパーミッションを設定する。作業例は以下。実ユーザー名はVPS上で確認する。
4. **別ターミナル**から新ユーザーの鍵認証でSSH接続できることを先に検証。現在の管理セッションは開いたまま維持する。
5. `sudo sshd -t` と `sudo sshd -T` で構文と実効設定を確認し、バックアップした`/etc/ssh/sshd_config`または`sshd_config.d/*.conf`に`PermitRootLogin no`, `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`を追加。別セッションの接続成功後に`sudo systemctl reload ssh`。接続不能なら既存セッション・DigitalOceanコンソールで戻す。OSごとの`Match`節や`AuthenticationMethods`の上書きに注意する。

```bash
# 以下は新規Dropletのコンソールでのみ適用する参考例。
sudo adduser bot-admin
sudo usermod -aG sudo bot-admin
sudo install -d -m 700 -o bot-admin -g bot-admin /home/bot-admin/.ssh
# authorized_keysには本人のPUBLIC KEYのみを安全に追加し chmod 600/chown を行う
sudo sshd -t
sudo sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication) '
# ここで管理元Macの第2セッションが成功していることが必須
```

## 2. DigitalOcean Cloud Firewall とUFW

- Cloud Firewallの受信: TCP/22は管理元IPv4 `ADMIN_IP/32`、必要なら確認済みIPv6 `ADMIN_IPV6/128`のみ。8097,8000,5432とDocker内部通信ポートは**インターネットへ開かない**。SSH以外の外部公開は今回不要。
- VPSのUFWでも受信既定拒否、SSHは上記の管理元だけ許可する。既存のSSH接続が確立できない状態でUFWを有効化しない。
- Dockerはポート公開時にホストのiptables/nftablesを操作する。**UFWの許可拒否だけでコンテナが守られるとは扱わない**。Cloud Firewall、`docker compose config --format json`、`docker ps`、`ss -ltnp`、管理外ネットワークからの到達拒否をそれぞれ確認する。
- `[::]:8097`, `0.0.0.0:8097`, TCP/8000, TCP/5432のホスト待受があれば**停止して修正**。IPv6でのDocker公開経路・Cloud Firewallルールも確認する。
- egressはCoincheck WebSocket/HTTPSニュース配信に必要な通信、DNS、NTPとOS更新先だけを検討。DBコンテナに外向き経路は設けない。

## 3. MacからのSSHトンネル（無料）

```bash
# VPS_IPと秘密鍵ファイルの場所は、正規のDigitalOcean管理情報で置換する。
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes \
  -i ~/.ssh/REAL_BOT_ADMIN_KEY \
  -L 127.0.0.1:18097:127.0.0.1:8097 \
  bot-admin@VPS_IP
```

接続後、Macブラウザで `http://127.0.0.1:18097` を開く。画面に入力するトークンはURLや`localStorage`へ保存しない。閲覧専用トークンで日常確認し、管理トークンを必要な停止操作のときだけ使う。

**トンネル切断試験（VPS作成後のみ）**：接続中にVPS側`docker compose ps`と市場受信件数を記録→MacのSSHとブラウザを閉じる→別のSSHセッションで再接続→同じDB件数が増加しコンテナが稼働していることを確認。実際のVPSでは**未検証**。トンネルを閉じたことでBotが止まる構成は不合格。

## 4. 管理元IP変更・鍵紛失・緊急復旧

- 管理元IPが変わる前にDigitalOcean Cloud Firewallの22番許可IPを更新し、旧許可を狭める。外出先で突然IPが変わった場合はDigitalOceanの**認証済みコンソール/Recovery Console**から本人確認して修正する。
- SSH到達不能時に安易に`0.0.0.0/0`で開放しない。どうしても必要なら期限付きの狭い許可、接続検査後の即時閉鎖、証跡を残す。
- 鍵紛失・侵害時はauthorized_keysを絞って対象鍵を失効し、新鍵で別セッション接続を確認。SSHホスト鍵変更を無条件で承認しない。
- DigitalOceanアカウントのMFA、Cloud Firewall更新権限とBotの秘密、バックアップ削除権限を分離する。

## 5. IPv6 / NTP / OS更新 / 稼働監視

```bash
ip -brief address
ss -ltnp
timedatectl status
systemctl status systemd-timesyncd --no-pager
sudo apt update
apt list --upgradable
docker compose ps
docker compose logs --tail=40 market news api
```

UTCでの2時間足/4時間足境界が基準。時刻同期ずれは市場データ品質を損なう。OSやDockerを更新する場合はバックアップ・復元可否・Rollback/HAの準備後に実施し、`down -v`は**禁止**。

## 6. 将来のiPhone・WireGuard

WireGuardは無料のソフトウェアとして、後から同じVPSの独立したVPNインターフェースへ追加できる。**VPNに接続しただけではVPSの127.0.0.1:8097にiPhoneから直接接続できない**。iPhone用にはWireGuardアドレス限定で認証・レート制限付きの小さなリバースプロキシを別サービスとして設定し、VPSのローカル8097へ転送する必要がある。VPNアドレスやトンネルIP、IPv6の実効待受とCloud Firewallを確認し、`0.0.0.0`バインドに変更しない。Tailscale・有料VPN・公開トンネルは必須ではない。

## 7. 受入 / 未検証

2026-11-03に隔離iMac Dockerで`127.0.0.1:18098`のホスト待受・DB/API非公開・ネットワーク内部属性を確認済み。**VPS用SSH鍵認証、Cloud Firewall、UFW、外部到達性、SSH切断継続、WireGuard、OS更新、時刻同期のVPS実機確認はすべて未実施**。Droplet未作成のためPASSとは報告しない。
