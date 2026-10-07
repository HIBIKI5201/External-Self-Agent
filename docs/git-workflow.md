# Git 運用規定

E.S. Agent の変更は **GitHub の Issue から始まり、ブランチ → Pull Request → main** の順で取り込む。
1 人開発で、作業の多くを AI エージェント（Claude Code）が行うことを前提にしている。

## 1. Issue が起点

- 改善案・不具合・相談は、まず **Issue** にする。会話やファイル（TODO など）に溜めない。
- Issue には種類のラベルを 1 つ付ける。

  | ラベル | 用途 |
  |---|---|
  | `feature` | 新しい機能・体験 |
  | `bug` | 期待と違う動き |
  | `dev-env` | 開発環境・ハーネス・運用 |
  | `spec` | 仕様の決定・整理だけ（コード変更なし） |
  | `question` | 決めることがある相談 |

- 決めるべきことがある Issue は、選択肢と推奨を本文に書き、決定をコメントに残してから着手する。

## 2. ブランチ

- `main` に直接コミットしない。`main` は常に「常駐させて動く状態」を保つ。
- 作業ブランチの名前は `<種類>/<Issue番号>-<短い英語名>`。

  | 種類 | 例 |
  |---|---|
  | `feature/` | `feature/12-admin-page` |
  | `fix/` | `fix/15-focus-window-front` |
  | `dev/` | `dev/3-claude-harness` |
  | `spec/` | `spec/20-category-context` |

- AI エージェントも同じ規則に従う。エージェント用の別系統（`agent/…`）は作らない。

## 3. コミット

- 1 コミット = 1 つのまとまった変更。動かない途中状態をコミットしない。
- メッセージは `<種類>: <何をしたか>（#Issue番号）`。種類は `feat` / `fix` / `docs` / `spec` / `chore` / `refactor`。日本語でよい。

  ```
  feat: 黒猫のダブルクリックでチャット画面を前面に出す（#7）
  ```

- 個人データ（`data/`・`secrets/`・`config.json`・`workspace/`）は `.gitignore` 済み。**絶対にコミットしない。**
  新しい設定項目を足したら `config.example.json` にも足す。

## 4. Pull Request

- 1 Issue = 1 PR を基本にする。PR は `main` へ向ける。本文は `.github/PULL_REQUEST_TEMPLATE.md` に従う。
- 本文の「クローズする Issue」に `Closes #番号` を書く。
- 挙動を変えたら、同じ PR で **`spec/feature/app-spec.md` を更新**する。新しいファイルは `spec/domains/*.domain.json` の `membership` に足す。
- 確認できたこと・できなかったことを分けて正直に書く（「多分大丈夫」で終わらせない）。
- マージは **Squash and merge**。マージ後はブランチを削除する。

## 5. 動作確認の約束

- 会話・タスク操作の検証は、**`data/esagent.db` のコピー**で行う（本番データは Google と同期している）。
- Google API の検証は偽の API に差し替える。実アカウントへの書き込みはユーザーの確認を取ってから。
- 常駐中のサーバーに変更を反映するときは、サーバーと黒猫を止めてからタスク `ESAgent` を起動する（`CLAUDE.md` 参照）。
