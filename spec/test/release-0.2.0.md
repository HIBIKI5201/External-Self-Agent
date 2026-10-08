# 0.2.0の検証と残る確認事項

2026-10-08、Issue #61。仕様の正本は `spec/feature/app-spec.md`、変更履歴は `CHANGELOG.md`、版は `VERSION`。

## 含めた変更と検証記録

| Issue / PR | 内容 | 記録 |
|---|---|---|
| #33 / #41 | 同じ秒の編集・送信中の再編集を同期に残す | `spec/test/sync-revisions-33.md` |
| #35 / #43 | ウィンドウ表示中に猫を隠し、最小化・終了で戻す。#10も利用者確認済み | `spec/test/pet-visibility-35.md` |
| #40 / #42 | 通信切断・再送で操作を重複させない | `spec/test/chat-retry-40.md` |
| #39 / #51 | 時間記録と遅延の診断 | `spec/test/message-timing-39.md` |
| #46 / #52 | 質問・否定を記憶保存と誤認しない | `spec/test/memory-intent-46.md` |
| #32 / #55 | 不正なモデル出力の検証と限定した再試行 | `spec/test/output-retry-32.md` |
| #53 / #56 | 会話で実際の繰り返し規則と次回締切を設定 | `spec/test/conversation-repeat-53.md` |
| #54 / #57 | 重複防止・新規操作・未反映を区別した返答 | `spec/test/duplicate-result-54.md` |
| #37 / #58 | YouTube検索・プレイリスト・アプリ内再生の実装 | `spec/test/youtube-2026-10-08.md` |
| #45 / #59 | 提案の承認・取消と話題の選択 | `spec/test/chat-choices-45.md` |
| #50 / #60 | 複数ターンの背景・条件、詳細の追記・訂正 | `spec/test/task-context-50.md` |

ログ診断CLI（#36）の記録は `spec/test/log-diagnostics-36.md`。

## リリース確認

- 開発・検証は稼働用とは別のチェックアウトと隔離DB・偽Google APIで行う。
- Python・JavaScript・JSON構文、送信キュー、差分の空白検査を確認する。
- `python tests/run.py --offline`: 82 passed, 0 failed, 14 skipped（モデル用）。`python tests/run.py`: 96 passed, 0 failed, 0 skipped。0.2.0のリリースブランチで実行。
- CI成功後にSquashマージし、稼働用を更新する。処理中でないことを確認してサーバーと猫を再起動し、読み取りAPIの正常応答を確認する。
- mainのリリースコミットに `v0.2.0` を付けてpushする。反映結果とコミットはPRのコメントへ記録する。

## 未確認・判断待ち

- #9: Google CloudでのOAuthアプリ公開と再接続。利用者による実アカウントの操作が必要。
- #37: YouTube APIの有効化、利用者の追加権限への同意、実プレイリストの取得。実装・偽API・公開動画の画面確認は完了しているが、Issueは開いたまま。
- #4・#5・#8・#44・#47・#48・#49: サービス・モデル・音声・長期記憶・成長期間・リポジトリ集約の設計判断待ち。決定前の機能をこの版に含めない。

実会話・実タスク・秘密情報の公開や、一括のデータ修復は行わない。ローカルの検証成功を、実アカウントの確認や将来の会話品質の保証と取り違えない。
