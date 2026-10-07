# AGENTS.md

AI エージェント向けの入口。詳細は `CLAUDE.md` に集約している（Claude 以外のエージェントも同じ規則に従う）。

# 仕様
- 仕様の正本: `spec/feature/app-spec.md`
- ドメインとコードの対応: `spec/domains/*.domain.json`
- 既知のずれ: `spec/plan/spec-gaps.md`

# 開発の規則
- `CLAUDE.md`（構成・動作確認・反映手順・禁止事項）
- `docs/git-workflow.md`（Issue 起点、ブランチ名、コミット、PR）

# エージェントの出力
- 調査記録・検証結果は `spec/test/` または `spec/plan/` に置き、仕様書と同じ PR でコミットする。
- 新しい置き場所をリポジトリ直下に作らない。
