# CLAUDE.md — E.S. Agent 開発ハーネス

E.S. Agent（External Self Agent）は、この Windows PC に常駐するローカル LLM のタスク管理アシスタント兼作業コンパニオン。
コンパニオンの名前は **Es**（フロイトの「エス」）、姿はデスクトップの黒猫。目指す体験は「Chill with You: Lo-Fi Story」。

## まず読むもの

- 仕様の正本: `spec/feature/app-spec.md`（AS-01〜AS-20）。挙動を変えたら同じ変更で更新する。
- どのファイルがどの仕様か: `spec/domains/*.domain.json`
- 既知のずれ・未確認事項: `spec/plan/spec-gaps.md`
- Git の運用: `docs/git-workflow.md`（Issue 起点、`main` に直接コミットしない）
- **`C:\ESAgent` は稼働用（常に `main`）。開発は worktree の `C:\ESAgent-dev`（ポート 8766）で行う。** マージ後に `C:\ESAgent` で `git pull` して再起動する。

## 構成の要点

- Python 3.12 の**標準ライブラリだけ**で書く（追加パッケージを入れない）。画面は素の HTML/JS。
- サーバー `esagent/server.py`（127.0.0.1:8765）、デスクトップの黒猫 `esagent/pet.py`（tkinter）、
  モデルは Ollama の `qwen3:8b`（`esagent/llm.py`）。
- **判断はコードで行い、モデルには狭い役割だけを任せる**（`esagent/brain.py`）。
  8B モデルは嘘・作り話・手本の丸写しをする。モデルの出力は必ず検証してから使い、タスク操作後の返事はテンプレートにする。
  新しいモデル対策を入れたら、AS-02・AS-04 に書く。
- `/` の動きはファイルで定義する（`skills/<名前>/SKILL.md`）。固定動作は `esagent/actions.py`。

## 動作確認の約束

- テストは `python tests/run.py`（全部）／`python tests/run.py --offline`（モデル不要のみ、CI と同じ）。
  PR の前に必ず全部を実行する。不具合を直したら再現テストを足す。
- 本番の `data/esagent.db` は Google ToDo・カレンダーと同期している。**会話・同期の検証は DB のコピーで行う**。
  `tests/support.py` の `Sandbox` が、DB のコピー・偽の Google API（`FakeTasks` / `FakeCalendar`）・
  実 Google API の遮断・Ollama への送信内容の記録（`sb.llm_calls`）をまとめて用意する。
- 作業の進め方（Issue の選び方、自己マージの条件、判断待ちの扱い）は `docs/git-workflow.md` に従う。
  クローン直後は `git config core.hooksPath .githooks`（main への直接 push を止める）。
- 画面の確認は内蔵ブラウザで `http://127.0.0.1:8765/` を開く。メッセージを送ると本番のチャット履歴に残るので、送るならユーザーに断る。

## 常駐中のプロセスに変更を反映する

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ESAgent*server.py*' -or $_.CommandLine -like '*pet.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
Get-ChildItem C:\ESAgent -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -Confirm:$false
Start-Sleep 2; Start-ScheduledTask -TaskName ESAgent
```

- 画面（`esagent/static/*`）だけの変更は再起動不要。チャット画面を再読み込みすれば反映される。
- 黒猫はサーバーが起動する。タスクのジョブから外して起動しているので、サーバーだけ止めてもタスクは再起動できる。

## この環境での注意

- Claude アプリのシェルはサンドボックス内で動く。**ウィンドウの前面化やキー入力の送出は効かない**（ユーザー環境では動く）。
- `%APPDATA%` に新しくフォルダを作ると、Claude のパッケージ領域に振り替えられてほかのアプリから見えない。インストーラーはユーザーに実行してもらう。
- bash のヒアドキュメントで Python を流すと `\\` や `\n` が化けることがある。エスケープを含むパッチは、一度ファイルに書いてから実行する。
- `.bat` / `.cmd` は CRLF にする。
- Google の OAuth クライアントのシークレットを画面から読み取ってファイルに書くことはしない（ユーザーが置く）。

## してはいけないこと

- `data/`・`secrets/`・`config.json`・`workspace/` をコミットする。
- ユーザーの確認なしに、規約への同意、OAuth の許可、カレンダーやアカウントへの書き込みをする。
- モデルの返事に「追加しました」などの操作の名乗りを、実際の操作なしで残す（`brain._CLAIM_RE` で打ち消している）。
