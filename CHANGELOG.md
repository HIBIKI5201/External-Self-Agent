# 変更履歴

書き方は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、版は [セマンティック バージョニング](https://semver.org/lang/ja/) に従う。
PR ごとに「未リリース」へ追記し、版を上げるときに見出しを付け替える（`docs/git-workflow.md` §7）。

## [未リリース]

### 追加
- 自動テスト（`tests/`）: DB のコピーと偽の Google API で動くサンドボックス、オフラインのテスト（日付・繰り返し・固定スキル・モード・Google 同期・カレンダー）
- CI（`.github/workflows/check.yml`）: 構文チェックとオフラインのテスト
- main への直接 push を防ぐ pre-push フック（`.githooks/`）
- 開発ルールの追加（マージ権限、判断待ち、優先度、テスト、版管理）

## [0.1.0] - 2026-10-07

### 追加
- 初期インポート。E.S. Agent（External Self Agent）として `C:\ESAgent` に配置
- 会話（判定・検証・テンプレート返事）、作業／雑談モード、記憶、返事待ち
- タスク管理（Google ToDo 双方向同期、締切のカレンダー反映、繰り返しタスク）
- スキル（`skills/*/SKILL.md`）、Claude Code への引き継ぎと MCP
- デスクトップの黒猫、集中タイマー、なかよし度、YouTube の BGM、環境音
