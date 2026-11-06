# Claude の利用枠を E.S. Agent で見る（#4）

`/usage` や「Claude の残りは？」で、Claude（Pro/Max）の 5 時間の枠と 1 週間の枠の**使用率とリセット時刻**を答えます。
値は Claude Code がステータスラインのコマンドに渡す `rate_limits` だけを使います（公式に渡される値はこれだけです）。
**残りのトークン数は分からないので出しません。** 記録が古いときは、いつの値かを先に言います。

## 設定（一度だけ、利用者が行う）

`~/.claude/settings.json`（Windows では `C:\Users\<名前>\.claude\settings.json`）に次を足します。
すでに `statusLine` があるときは、その設定を残すかどうかを先に決めてください。

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:\\ESAgent\\tools\\claude_statusline.py"
  }
}
```

Claude Code で返事をもらうたびに、`C:\ESAgent\data\claude-usage.json` に使用率・リセット時刻・記録した時刻を書き、
ステータスラインに `5h 42% / 7d 18%` のように出します。会話の内容やファイルの場所は書きません。

## 注意

- `rate_limits` が渡されるのは Pro/Max のときで、セッションの最初の返事のあとです。API の利用では渡されません。
- Claude Code の版によって `rate_limits` が渡されないことがあります。そのときは「まだ記録がない」と答えます。
- `resets_at` は UNIX 秒と ISO 形式のどちらでも読みます。
