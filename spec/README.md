# E.S. Agent 仕様書

E.S. Agent の仕様と、その裏付けをまとめたディレクトリ。書き方は SymphonyKillChord の `spec/` に合わせている。

## 構成

| パス | 内容 |
|---|---|
| `feature/app-spec.md` | **仕様の本体**。AS-01〜AS-20 の規則と、未解決の一覧 |
| `domains/*.domain.json` | 機能のまとまり（ドメイン）ごとに、担当するコードのパスと仕様の節を対応づける |
| `plan/spec-gaps.md` | 仕様とコードのずれ、未確認事項、保留中の拡張案、解消済みの課題 |
| `test/verification-log.md` | どの仕様を、どう確かめたかの記録 |

## 読み方

`feature/app-spec.md` の各項目は、先頭の見出しで種類を示す。

- **決定** — ユーザーとの対話で決めた規則。仕様の正本。
- **意図** — その規則を置いた理由。
- **実装** — コードで確認できる事実（ファイル名つき）。
- **制約** — 外部サービスの規約や OS の制限など、変えられない条件。
- **未解決** — 決まっていない、または確認できていない事項。

## 更新のしかた

- 仕様を変えたら、`feature/app-spec.md` の該当する節を直し、未解決の一覧も見直す。
- 新しいファイルを足したら、該当する `domains/*.domain.json` の `membership` にパスを追加する。
- 実機で確認できたら `test/verification-log.md` の ⏳ を ✅ にし、`plan/spec-gaps.md` から外す。

## ドメイン一覧

| ドメイン | 担当 | 主な仕様 |
|---|---|---|
| runtime-hosting | 常駐・起動・設定・API の安全策 | AS-01, AS-17 |
| conversation-brain | 判定・返事・モード・日付・記憶・返事待ち | AS-02, AS-03, AS-06, AS-08, AS-16 |
| task-store | タスク・プロジェクト・繰り返し | AS-04, AS-05 |
| skills | `/` のスキル | AS-07 |
| google-integration | Google ToDo とカレンダー | AS-10, AS-11 |
| claude-handoff | Claude Code への引き継ぎと MCP | AS-09 |
| companion-focus | デスクトップの黒猫・集中タイマー・なかよし度 | AS-12, AS-13 |
| bgm-ambience | YouTube の BGM・環境音 | AS-14 |
| chat-ui | チャット画面 | AS-15, AS-16 |
