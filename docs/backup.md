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

4. サーバーを起動します（`Start-ScheduledTask -TaskName ESAgent`）。

コピーを取った時刻より後の会話・記憶・集中の記録は戻りません。タスクは Google ToDo と同期しているので、
次の同期で Google 側の変更が取り込まれます。
