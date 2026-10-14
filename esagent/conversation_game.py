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


# 「しりとりをしようよ。」「暇だから、しりとりしよう」「しりとりしない？」「りから始めて」: the model played
# these without the state when only a few exact phrasings started the game (#156).
_START_RE = re.compile(
    r"^(?:[^。！!？?\n]{0,15}[、,]\s*)?しりとり(?:を|で|でも)?\s*"
    r"(?:しよう|しよ|やろう|やろ|したい|やりたい|しない|しなーい|しなぃ|する|やる|始めよう|始めよ|始めて|開始|"
    r"遊ぼう|遊ぼ|に戻ろう|に戻して|して)?"
    r"(?:よ|ね|か|かな|ぜ|っ|ー|〜|～)*[。！!？?\s]*"
    r"(?:[、,]?\s*[「『]?(?P<head>[ぁ-ゖァ-ヺ])[」』]?から(?:始めて|始めよう|始める|で|ね|スタート)?[。！!？?\s]*)?"
    r"(?:[、,]?\s*(?P<who>君|きみ|キミ|あなた|Es|エス|俺|おれ|私|わたし|僕|ぼく|自分)から(?:ね|で|どうぞ)?[。！!？?\s]*)?$")
_USER_FIRST = ("俺", "おれ", "私", "わたし", "僕", "ぼく", "自分")


_END_RE = re.compile(r"(?:しりとり(?:を|は)?)?(?:もう)?(?:終了|終わり|終わろう|終わりにしよう|やめて|やめよう|やめよ|やめる|"
                     r"やめた|やめ|おしまい|もういい|飽きた)(?:よ|ね|か|かな|ー|〜|～)*[。！!？?\s]*")
# Saying what one is about to do next ends the game instead of being taken as a wrong word (#156).
_LEAVE_RE = re.compile(r"^(?:今から|いまから|これから)|"
                       r"(?:してくる|してきます|しに行く|やってくる|行ってくる|いってくる|寝る|寝ます|落ちる|"
                       r"するね|するよ|やるね|やるよ|します|始めるね|始めるよ)[。！!\s]*$")


def end_on_cancel():
    """「やめて」 during a game is sent as a cancel of the last request; it also ends the game (#156)."""
    if not state():
        return False
    save({})
    return True


def start(text):
    """The game's first state for a start request, or None."""
    m = _START_RE.match(text)
    if not m or not re.search(r"しりとり(?:を|で|でも)?\s*(?:しよう|しよ|やろう|やろ|したい|やりたい|しない|しなーい|しなぃ|する|やる|"
                              r"始め|開始|遊ぼ|に戻|して|[。！!？?\s]*$)", text):
        return None
    head = reading(m["head"]) if m["head"] else None
    first = "user" if m["who"] in _USER_FIRST else "es"
    return {"activity": "shiritori", "turn": first, "last_word": "", "next": head or ("" if first == "user" else "げ"),
            "used": []}


def handle(text):
    text = text.strip()
    value = state()
    begun = start(text)
    if begun:
        if begun["turn"] == "es":
            return _answer(begun)
        save(begun)
        head = f"「{begun['next']}」から" if begun["next"] else "好きな言葉から"
        return f"しりとり始めよう！あなたからだね。{head}どうぞ。"
    if not value:
        return None
    if _END_RE.fullmatch(text):
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
    if not sound and _LEAVE_RE.search(text):
        # 「今からWarframeするよ」: the user moved on; the usual conversation answers it (#156).
        save({})
        return None
    if not sound:
        return "しりとり中だよ。一語だけ答えてね。漢字の言葉は「漢字（よみ）」の形で読みも教えて。終了するなら「しりとりをやめて」と言ってね。"
    if value["next"] and sound[0] != value["next"]:
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
