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

### 稼働用と開発用の作業ツリーを分ける
- 常駐中の E.S. Agent は `C:\ESAgent` の作業ツリーから直接動く。ここでブランチを切り替えると稼働中のアプリが変わるので、**`C:\ESAgent` は常に `main` のまま**にする（ブランチを切らない・編集しない）。
- 開発は git worktree の **`C:\ESAgent-dev`** で行う。
  ```
  git -C C:\ESAgent worktree add C:\ESAgent-dev -b <ブランチ> origin/main
  ```
  `C:\ESAgent-dev\config.json` はポート `8766`・`desktop_pet: false` にして、稼働中のサーバーや黒猫とぶつからないようにする。
- マージしたら `C:\ESAgent` で `git pull` し、常駐プロセスを再起動して反映する（§9）。

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
- `Closes #番号` で Issue が自動で閉じないことがある。マージ後に Issue が開いたままなら、マージした PR を書き添えて手動で閉じる。
- `CHANGELOG.md` の「未リリース」に 1〜数行で追記する。

### マージ権限
- **AI エージェントは自分の PR を自己マージしてよい**。条件は次のすべて。
  - PR テンプレートの必須チェックがすべて埋まっている
  - CI（`check`）が成功している
  - モデルを使うテスト（`python tests/run.py`）を手元で実行し、失敗が無い
  - Issue に `needs-decision` が付いていない（ユーザーの判断待ちの論点が残っていない）
- 次の変更は**ユーザーの承認を待ってからマージ**する: 外部サービスの設定・権限・課金に関わるもの、データを消す・移すもの、Git 運用規定そのものの変更。
- マージ後、常駐中の E.S. Agent に反映し、反映したことを PR にコメントする。

## 5. 判断待ちと優先度

- ユーザーが決めることがある Issue には `needs-decision` を付け、本文かコメントに**選択肢・それぞれの利点と代償・推奨**を書く。エージェントは決まるまで実装に入らず、次の Issue に進む。決定はコメントに残し、ラベルを外す。
- 優先度ラベル: `priority:high`（体験を壊している・データに関わる）／`priority:medium`（通常）／`priority:low`（あると嬉しい）。
  エージェントは **high → medium → low、同じなら番号の小さい順**に着手する。
- 実機でしか確認できないもの（画面の前面化、デスクトップ表示など）は `needs-verification` を付け、確認手順を書いてユーザーに依頼する。

## 6. テストと CI

- テストは `tests/` に置き、`python tests/run.py` で全部、`python tests/run.py --offline` でモデル不要のものだけ実行する。
  サンドボックス（`tests/support.py`）が DB のコピー・偽の Google API・送信内容の記録を用意する。
- モデルを使うテスト関数には `@needs_llm` を付ける（Ollama が無い環境では自動でスキップ）。
- 不具合を直したら、その不具合を再現するテストを足す。会話の振る舞いを変えたら、関連する会話テストを足す。
- CI（GitHub Actions `check`）は PR と main への push で、構文チェック（Python・JS・JSON）とオフラインのテストを実行する。モデルを使うテストは CI では動かない（手元で実行して PR に結果を書く）。

## 7. 版と変更履歴

- 版は `VERSION`（セマンティック バージョニング）。挙動を変えた PR がまとまったら、エージェントが版を上げて `CHANGELOG.md` の見出しを付け替え、`main` に `v<版>` のタグを付ける。
  - 新しい機能 → minor（0.1.0 → 0.2.0）、不具合修正だけ → patch（0.1.0 → 0.1.1）
- タグの push は `git push origin v<版>`（main への直接 push ではないのでフックに止められない）。

## 8. main への直接 push の防止

- 非公開リポジトリの無料プランでは GitHub のブランチ保護が使えないため、**ローカルの pre-push フック**（`.githooks/pre-push`）で止める。
  クローンしたら一度 `git config core.hooksPath .githooks` を実行する。

## 9. 動作確認の約束

- 会話・タスク操作の検証は、**`data/esagent.db` のコピー**で行う（本番データは Google と同期している）。`tests/support.py` の `Sandbox` を使う。
- Google API の検証は偽の API に差し替える。実アカウントへの書き込みはユーザーの確認を取ってから。
- 常駐中のサーバーに変更を反映するときは、サーバーと黒猫を止めてからタスク `ESAgent` を起動する（`CLAUDE.md` 参照）。
