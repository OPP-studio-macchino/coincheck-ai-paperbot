# 追補 — セキュリティ候補版・最終検収 (2026-11-03 JST)

この資料は **改修ブランチの試験結果** であり、既存稼働Botのセキュリティ改修が完了したことやDigitalOceanに配備済みであることを示さない。

## 17時台の再確認

- 検査対象は外付けHDDの `coincheck-ai-paperbot-security` worktree、ブランチ `security/do-security-hardening-20261103`。
- 元Bot `coincheck-ai-paperbot` の `main` HEADは改修前 `9ab6bf79fb07342ae6026e332d0a2b72081d6cce`、未コミット変更なし。**元のDocker/DB/market/newsを変更・停止していない**。RDCのDockerコンテキストを区別して確認。
- 隔離Composeの5サービス (db/api/market/news/frontend) は起動。DB/APIはhealthy、market/newsはup、フロントホスト公開は `127.0.0.1:18098` だけ。DBとAPIのホスト公開はなし。実ネットワーク `db_private` と `front_edge` は `internal:true`。frontendはDBに直結しない。
- 単体試験 **32件 PASS**、Python/JavaScript構文・Compose構文・Git diff形式チェック PASS。Git履歴を含むsecretスキャンは値非出力でPASS。
- 管理画面の認証無しGET→401、閲覧資格GET→200/POST→403、管理資格POST→200、Origin・Host拒否、監査記録との一致、操作後の元pause状態への復元は隔離HTTPでPASS。
- 市場データは実Coincheck**公開**WebSocketのみを使用。隔離DBに約定・足の受信がある。初期履歴不足は `history_ready=false` と理由を表示する。データや価格が古い場合は評価額を表示しない。
- 完全独立fixture用DBによるDBロール境界/同足買い競合/二重約定/STOP時のATR決済/ニュース不明時の新規購入抑止はPASS（`app/security_e2e.py`、`app/fixture_trades.py`）。market / news / query / controlロールの非許可更新・DDLを拒否。
- 元の稼働DBを**読み取り専用でdump**し、隔離PostgreSQLの新DB `paperbot_restore_20261103` に戻す試験PASS。追加migration・権限ロールバック・再適用で二つのportfolioのMD5は `56dab92976f06360ec370f8acd1724f4` のまま不変。元の収集が継続していることを確認。
- `pip-audit==2.9.0` を利用した固定版 `requirements.lock` の公開脆弱性アドバイザリ検査は **No known vulnerabilities found**（検査時点。将来の脆弱性不存在を意味しない）。Python固定版は揃っているが全wheelのハッシュは未固定。
- age公開鍵で暗号化した `.dump.age` と SHA256ファイル生成を確認。**オフライン復号用private keyを別保管に分離して行う `restore_clone.sh` の実行検収は未完了**。鍵の移動操作はリモート安全チェックで拒否されたため代替経路で迂回していない。異なる障害ドメインへの転送とRPO/RTOも未実施。

## 確認できなかったもの

- APIコンテナ再起動を跨いだ停止POSTの**HTTP実検証**はRDCの安全チェックで拒否され、迂回していない。DB状態の永続化・停止と購入の同時トランザクションの競合検証はPASS。
- DigitalOcean Dropletは未作成。SSH鍵認証・Cloud Firewall・UFW・IPv6外部到達性・NTP・OS更新・SSHトンネル切断後のVPS独立稼働は**未確認**。
- Coincheck private API / 実注文 / OpenAI有料APIの実試験は**禁止のため未実施**。ニュースはmock（unknown→news_gateの新BUY抑止）。liveニュースは独立記事検証器がないため `clear` を与えない。
- コンテナイメージの包括的CVE/脆弱性スキャンと、外部ネットワークからの侵入試験は未実施。
- GitHub mainブランチの保護設定はGitHub APIで `Branch not protected`（HTTP 404）。`required status checks` を必須とするGitHubルールの設定は**未実施**。公開ブランチCIの結果はpush後に確認する。

## 作業者向け出荷ゲート

- 本体に反映しない。まずこの改修ブランチのHEAD、公開CI、秘密走査、依存監査・Dockerの起動証跡を確認。
- DigitalOceanへのSSH・Cloud Firewall・known_hosts設定はVPSを作ってから個別に検収。8097はループバック、5432/8000はホスト非公開を実測。
- 復号鍵とバックアップの保管権限を分離したうえで、別DBへの**暗号化アーカイブからの復元**を通し、RPO/RTOを初めて実測する。
- VPS配備が承認されるまで `DRY_RUN=true`, `LIVE_TRADING_ENABLED=false`, `NEWS_MODE=mock`, 実注文・課金API使用0を維持する。
- 独立検査結果を製品保証と取り違えず、未確認をPASSにはしない。

**現時点の判定：コード改修あり／隔離環境で部分的に検証済み／稼働中環境は未反映／VPSは未検証・NO-GO。**
