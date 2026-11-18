---
name: profile
description: Es が記録から見ているキミの傾向（就寝・タスクを終える時間帯）。直す・消す・戻すもできる
argument-hint: (forget|restore|unfix 名前 | fix 名前 値)
action: profile
---
記録をコードで集計した観察を、根拠つきで一覧にする。`/profile forget bedtime` で使わないようにし、`/profile restore bedtime` で戻す。違っていれば `/profile fix bedtime 1:30` で直し、`/profile unfix bedtime` で記録に戻す。
