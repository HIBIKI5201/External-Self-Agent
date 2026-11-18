# データベースのバックアップと戻し方（#306）

E.S. Agent は `data/esagent.db` を 1 日 1 回、`data/backups/esagent-YYYYMMDD.db` にコピーします。

- サーバーが動いている日の最初の機会に取ります（PC を落としていた日は、起動して 1 分ほどのうちに）。
- SQLite のバックアップ機能で取るので、動作中でも一貫したコピーになります。
- 取ったコピーは `PRAGMA integrity_check` で確かめ、`ok` でなければ捨ててログに `backup discarded` を残します。
- 7 日分を残し、古いものから消します。
- ディスクの空きが DB の大きさの 3 倍より少ないときは取らず、ログに `backup skipped` を残します。

## 戻し方

1. サーバーと黒猫を止めます（`CLAUDE.md` の「常駐中のプロセスに変更を反映する」の 1 行目）。
2. 今の `data/esagent.db` を念のため別の名前で残します（例: `esagent.broken.db`）。
   `data/esagent.db-wal` と `data/esagent.db-shm` があれば、同じく別の名前に移します（戻したコピーに古い WAL が混ざらないように）。
3. 戻したい日のコピーを `data/esagent.db` という名前で置きます。

   ```powershell
   Copy-Item C:\ESAgent\data\backups\esagent-20261117.db C:\ESAgent\data\esagent.db
   ```

4. **サーバーを起動する前に**、戻したコピーで Google ToDo にまだ送っていなかったタスクを確かめます。
   サーバーが止まっている間は同期も止まっています。

   ```powershell
   python -c "import sqlite3; c = sqlite3.connect(r'C:\ESAgent\data\esagent.db'); [print(*r) for r in c.execute('SELECT id, title FROM tasks WHERE gtask_id IS NULL')]"
   ```

   ここに出たタスクは、コピーを取ったあとに Google へ送られたかもしれません。そのまま起動すると、
   Google から取り込んだものと、戻したコピーから送り直したものが両方に重複します。
   戻したコピーのタスクは消しません（繰り返しの設定、Google に入りきらない長いメモ、Claude への引き継ぎの記録は
   こちらにしかありません）。代わりに、**Google ToDo（スマホやブラウザ）で、同じタスクのほうを消してください**。
   起動すると、戻したコピーのタスクが送り直されます。

   - Google ToDo にないタスクは、何もしなくて構いません（起動後に送られます）。
   - 同じタスクかどうか分からない（名前は同じでも、コピーのあとに Google 側で完了やメモの書き足しをした）ときは、
     まだ起動せず、Google 側の内容を書き留めてから消すか、Google 側を残して重複を受け入れるかを決めてください。

5. サーバーを起動します（`Start-ScheduledTask -TaskName ESAgent`）。

コピーを取った時刻より後の会話・記憶・集中の記録は戻りません。タスクは Google ToDo と同期しているので、
次の同期で Google 側の変更が取り込まれます。
