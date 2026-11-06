# AI自己改善エンジン

**仕様の正本：[SOURCE_OF_TRUTH.md](docs/ai-self-improvement/SOURCE_OF_TRUTH.md) — `ai-self-improvement-v0.2`**

既存Coincheck Botと同じリポジトリで開発し、研究の実行権限・台帳・資金を分離します。通常のBotは`DRY_RUN=true`、`LIVE_TRADING_ENABLED=false`、`NEWS_MODE=mock`を維持します。

| 資料 | 役割 |
|---|---|
| [正本](docs/ai-self-improvement/SOURCE_OF_TRUTH.md) | 目的、変更可能範囲、費用・権限・採否の規則 |
| [計画書](docs/ai-self-improvement/PLAN.md) | Phase別の成果物、受入条件、残件 |
| [技術書](docs/ai-self-improvement/TECHNICAL_DESIGN.md) | データ、評価、要求書・成果物、API、二つの台帳 |
| [運用手順](docs/ai-self-improvement/OPERATIONS.md) | fixture実行、明示live有効化、分離、保全、戻し方 |
| [統合現況](docs/INTEGRATION_STATUS_20261106.md) | Bot・AI・関連案件の出自、統合範囲、稼働状態の区別 |
| [MVP検収記録](docs/ai-self-improvement/ACCEPTANCE_20261105.md) | 2026-11-05時点の過去証跡 |
| [Phase 2検収記録](docs/ai-self-improvement/ACCEPTANCE_PHASE2_20261106.md) | 対象コード、実行した検証、未確認の範囲 |

本版の既定動作は、通信なし・秘密なしの固定応答です。Phase 2の経路は **要求書作成 → 別プロセスで1候補生成 → 成果物を照合してオフライン評価**。実AIの多段自動反復、定期実行、自動採用、稼働コードの自己書換えは含みません。

live生成だけが専用キーと有限予算を必要とします。キーや台帳が不足した場合に自動接続するfallbackはありません。fixtureのモデル名・usage・費用は模擬値で、実APIや実支出の証拠ではありません。

コードのmerge、実API成功、利益改善、VPSへのリリースは別の判定です。検収記録にない完了を、この入口文書から推定しないでください。
