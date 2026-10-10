"""A small, deterministic shiritori activity; no task or model operations (#108)."""
import json
import re
import unicodedata

import db

# Explicit readings only: unknown kanji require a reading rather than a guessed pronunciation.
WORDS = """ゲーム ムード ドア アイス スイカ カラス スズメ メダカ カメ メガネ ネズミ ミルク クイズ ズボラ ラッパ パンダ ダーツ ツバメ メモ モモ モグラ ラクダ ダンス スキー キツネ ネコ コアラ ラジオ オクラ ラムネ ネギ ギター タヌキ キノコ コマ マスク クルミ ミミズ ズッキーニ ニワトリ リス スープ プール ルビー ビール ルーペ ペンギン ギンガ ガラス スミレ レモネード ドラム ムギ ギンナン ウサギ ゴリラ ワニ ニジ ジグザグ グラス クマ マリ リンゴ ゴマ マグロ ロバ バラ ライム ムシ シカ カイ イカ カニ ニラ ラッキョウ ウリ リキシ シマウマ マチ チズ ズコウ ウタ タコ コメ メザシ シジミ ミカヅキ キリ リンカク クキ キク""".split()
READINGS = {w: w for w in WORDS}
READINGS.update({"林檎": "リンゴ", "りんご": "リンゴ", "猫": "ネコ", "犬": "イヌ"})
SMALL = str.maketrans("ぁぃぅぇぉゃゅょっゎゕゖ", "あいうえおやゆよつわかけ")


def reading(word):
    value = unicodedata.normalize("NFKC", READINGS.get(word, word))
    if not re.fullmatch(r"[ぁ-ゖァ-ヺー]+", value):
        return None
    value = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)
    value = value.translate(SMALL)
    # A terminal long mark continues from the preceding kana, as an explicit house rule.
    return value.rstrip("ー") or None


def state():
    try:
        value = json.loads(db.get_setting("conversation_game") or "{}")
        return value if value.get("activity") == "shiritori" else {}
    except (ValueError, AttributeError):
        return {}


def save(value):
    db.set_setting("conversation_game", json.dumps(value, ensure_ascii=False))


def _answer(value):
    head = value["next"]
    for word in WORDS:
        sound = reading(word)
        if sound[0] == head and sound[-1] != "ん" and sound not in value["used"]:
            value["used"].append(sound)
            value.update(last_word=word, next=sound[-1], turn="user")
            save(value)
            return f"「{word}」。次は「{sound[-1]}」だよ。"
    save({})
    return f"「{head}」からの未使用の言葉が思いつかなかった。私の負け。しりとりは終了だよ。"


def handle(text):
    text = text.strip()
    value = state()
    if re.fullmatch(r"(?:しりとり)(?:を|で)?(?:しよう|しよ|やろう|したい|して|始めて|開始|に戻ろう|に戻して)?[。！!\s]*", text):
        value = {"activity": "shiritori", "turn": "es", "last_word": "", "next": "げ", "used": []}
        return _answer(value)
    if not value:
        return None
    if re.fullmatch(r"(?:しりとりを|しりとりは)?(?:終了|終わり|やめて|やめよう|おしまい)[。！!\s]*", text):
        save({})
        return "しりとりは終了だよ。"
    if re.search(r"(?:クイズ|雑談|作業モード|雑談モード|別の話)(?:に|へ)(?:切り替|変え|戻)", text):
        save({})
        # Explicit switch is released to the usual conversation path.
        return None
    if re.search(r"次は(?:君|きみ|あなた|Es|エス)|(?:君|きみ|あなた|Es|エス)の(?:手番|番)|交互に", text):
        if value.get("turn") == "es":
            return _answer(value)
        return f"交互に一語ずつだね。私の答えは「{value['last_word']}」。今はあなたの番で「{value['next']}」からだよ。"
    literal = text.strip("「」『』。！! ")
    match = re.fullmatch(r"(.{1,30})[（(](?:読み[は:：]?)?([ぁ-ゖァ-ヺー]+)[）)]", literal)
    word, sound = (match[1], reading(match[2])) if match else (literal, reading(literal))
    if not sound:
        return "しりとり中だよ。一語だけ答えてね。漢字の言葉は「漢字（よみ）」の形で読みも教えて。終了するなら「しりとりをやめて」と言ってね。"
    if sound[0] != value["next"]:
        return f"今は「{value['next']}」からだよ。もう一度どうぞ。"
    if sound in value["used"]:
        return f"「{word}」はもう使ったよ。別の言葉をどうぞ。"
    if sound[-1] == "ん":
        save({})
        return f"「{word}」は「ん」で終わるから、あなたの負け。しりとりは終了だよ。"
    value["used"].append(sound)
    value.update(last_word=word, next=sound[-1], turn="es")
    save(value)
    return _answer(value)
