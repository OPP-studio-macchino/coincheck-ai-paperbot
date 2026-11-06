# CryptoFX PHASE 3 — SSH / UFW 実施証跡

実施日: 2026-11-04 JST

対象: DigitalOcean Droplet `CryptoFX`（ID `615557110`）
範囲: SSH 管理経路とホスト Firewall のみ。Docker、Bot、PostgreSQL、API 資格情報、実注文には変更を加えていない。

## 実施済み

- DigitalOcean API で Droplet ID、名称、公開 IPv4、Ubuntu 24.04 を再照合した。
- Recovery Console で確認済みの ED25519 ホスト鍵指紋と SSH 接続先の公開鍵を照合し、一致後に `known_hosts` へ登録した。
- `coincheck-paperbot-fw` を対象 Droplet のみに適用した。受信許可は、実施時点の管理端末 IPv4 を `/32` で指定した TCP/22 のみ。TCP 80、443、5432、8000、8097 を含む他の受信許可は作成していない。
- `bot-admin` を作成し、確認済み管理公開鍵を `~bot-admin/.ssh/authorized_keys` に登録した。最終権限は `.ssh` が `0700`、`authorized_keys` が `0600`、いずれも `bot-admin:bot-admin`。
- `bot-admin` を標準の `sudo` グループに追加した。個別 sudoers や NOPASSWD 設定は追加していない。
- UFW を incoming deny / outgoing allow に設定し、有効化した。唯一の受信許可は実施時点の管理元 IPv4/32 から TCP/22。
- UFW 有効化後、新規 `bot-admin` 鍵 SSH セッションと既存 root 鍵 SSH セッションが成功した。

## 実測コマンドと結果

| 検査 | 結果 |
|---|---|
| `ssh-keyscan -t ed25519 ... | ssh-keygen -lf -` | Recovery Console の信頼済み ED25519 指紋と一致 |
| `ssh bot-admin@... 'id'` | 鍵認証成功。UID/GID `bot-admin`、`sudo` グループ所属 |
| `ufw status verbose` | active、incoming deny、outgoing allow |
| `ufw status numbered` | TCP/22 に管理元 IPv4/32 だけを許可 |
| `ss -lntup`（変更前監査） | 公開待受は SSH 22 のみ。Docker 未導入、実行コンテナなし |
| `sudo -n -v`（bot-admin） | パスワード要求となり、資格情報なしでの昇格なし |

## 未完了と安全な再開条件

`bot-admin` のパスワードは意図的に設定していないためロック状態である。したがって、通常の sudo パスワード認証を実成功として検証できていない。

このため `PermitRootLogin no` は**未適用**である。root SSH を無効化する前に、所有者が DigitalOcean Recovery Console 等の保護された対話経路で `bot-admin` のパスワードを設定し、別セッションで `sudo -v` の成功を確認する必要がある。パスワード、秘密鍵、トークンはこのリポジトリや証跡に保存しない。

再開時は、管理元 IP を再取得して Cloud Firewall/UFW の `/32` と照合し、`sshd -t`、`sshd -T`、新規 `bot-admin` SSH・sudo を確認してから `PermitRootLogin no` を適用・reload する。失敗時は既存 root セッションまたは DigitalOcean Recovery Console で `/etc/ssh/sshd_config.d/` の変更を戻す。

## 現在の判定

上記の時点では、通常 sudo の認証確認待ちとして一時的に BLOCKED とした。次節の最終検収がこの状態を更新する。

## 最終検収

- 所有者が保護された DigitalOcean Recovery Console 経路で `bot-admin` のパスワードを設定し、通常の `sudo whoami` が `root` を返すことを確認した。パスワード自体は取得・記録していない。
- 再照合で Droplet ID、名称、公開 IPv4、OS、ED25519 ホスト鍵、管理端末 IPv4/32、Cloud Firewall の適用先がすべて一致した。
- `bot-admin` の新規鍵 SSH セッションを再確認した。SSH は active。
- `/etc/ssh/sshd_config.d/99-opp-security.conf` に `PermitRootLogin no`、`PasswordAuthentication no`、`KbdInteractiveAuthentication no`、`PubkeyAuthentication yes` を設定した。`sshd -t` と `sshd -T` で実効値を確認してから reload した。
- root の鍵 SSH は `Permission denied (publickey)` で拒否された。root パスワード・認証回避・総当たりは実施していない。
- Cloud Firewall は管理端末 IPv4/32 から TCP/22 だけを許可し、UFW は active、incoming deny / outgoing allow、同じ TCP/22 許可だけであることを確認した。SSH 設定変更後に UFW ルールは変更していない。
- 管理端末から TCP 80、443、5432、8000、8097 の到達不能を実測した。ホスト待受は SSH 22 と loopback DNS のみ。

### 復旧

SSH による管理不能時は DigitalOcean Recovery Console を使う。`/etc/ssh/sshd_config.d/99-opp-security.conf` を修正または削除し、`sshd -t` が成功することを確認してから `systemctl reload ssh` を実行する。Cloud Firewall の TCP/22 許可は、管理端末の現在の IPv4/32 だけに更新してから再接続する。広域 CIDR や `0.0.0.0/0` を緊急回避として追加しない。

**PHASE 3: PASS** — `bot-admin` の鍵 SSH・通常 sudo、Cloud Firewall、UFW、root SSH 拒否、不要受信ポートの不達、再接続を検収済み。PHASE 4（Docker 導入）へ進行可能。ただし、Docker のポート公開が UFW を迂回し得るため、Docker 導入時に Cloud Firewall、Compose ポート定義、`ss -lntup`、外部到達性を改めて検収する。
