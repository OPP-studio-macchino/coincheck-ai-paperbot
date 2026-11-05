# AI自己改善エンジン

**正本： [docs/ai-self-improvement/SOURCE_OF_TRUTH.md](docs/ai-self-improvement/SOURCE_OF_TRUTH.md)**

仕様の優先順位、変更権限、実装済み範囲、将来の拡張は上記の正本で管理します。
この入口ファイルに別の仕様を複製しません。

| 資料 | 内容 |
|---|---|
| [正本](docs/ai-self-improvement/SOURCE_OF_TRUTH.md) | 不変条件、候補の権限、採否とリリースの境界 |
| [計画書](docs/ai-self-improvement/PLAN.md) | 段階別の成果物・完了条件・残る課題 |
| [技術書](docs/ai-self-improvement/TECHNICAL_DESIGN.md) | データ、指標、評価、API契約、台帳、障害復旧 |
| [実行・運用手順](docs/ai-self-improvement/OPERATIONS.md) | オフラインMVP、独立Docker、停止・再実行 |
| [検収書](docs/ai-self-improvement/ACCEPTANCE_20261105.md) | 今回の実測結果、未検証の範囲 |

MVPの既定経路は**通信なし・固定応答・研究専用**です。既存Botの戦略・ポジション・DBを変更しません。
実装は [`ai_lab/`](ai_lab/)、実行方法は運用手順を参照してください。
