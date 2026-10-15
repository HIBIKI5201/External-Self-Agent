"""A small, deterministic shiritori activity; no task or model operations (#108)."""
import json
import re
import unicodedata

import db

# Explicit readings only: unknown kanji require a reading rather than a guessed pronunciation.
WORDS = """ゲーム ムード ドア アイス スイカ カラス スズメ メダカ カメ メガネ ネズミ ミルク クイズ ズボラ ラッパ パンダ ダーツ ツバメ メモ モモ モグラ ラクダ ダンス スキー キツネ ネコ コアラ ラジオ オクラ ラムネ ネギ ギター タヌキ キノコ コマ マスク クルミ ミミズ ズッキーニ ニワトリ リス スープ プール ルビー ビール ルーペ ペンギン ギンガ ガラス スミレ レモネード ドラム ムギ ギンナン ウサギ ゴリラ ワニ ニジ ジグザグ グラス クマ マリ リンゴ ゴマ マグロ ロバ バラ ライム ムシ シカ カイ イカ カニ ニラ ラッキョウ ウリ リキシ シマウマ マチ チズ ズコウ ウタ タコ コメ メザシ シジミ ミカヅキ キリ リンカク クキ キク""".split()
READINGS = {w: w for w in WORDS}
READINGS.update({"林檎": "リンゴ", "りんご": "リンゴ", "猫": "ネコ", "犬": "イヌ"})
# Words voice recognition often returns in kanji (#169). Others get a reading from the model, checked in code.
READINGS.update({"虫": "ムシ", "感じ": "カンジ", "鳥": "トリ", "魚": "サカナ", "馬": "ウマ", "牛": "ウシ", "豚": "ブタ",
                 "熊": "クマ", "猿": "サル", "鹿": "シカ", "亀": "カメ", "蛇": "ヘビ", "蟹": "カニ", "海": "ウミ",
                 "山": "ヤマ", "川": "カワ", "空": "ソラ", "雨": "アメ", "雪": "ユキ", "星": "ホシ", "月": "ツキ",
                 "花": "ハナ", "木": "キ", "森": "モリ", "石": "イシ", "机": "ツクエ", "椅子": "イス", "傘": "カサ",
                 "鞄": "カバン", "靴": "クツ", "時計": "トケイ", "電車": "デンシャ", "車": "クルマ", "本": "ホン",
                 "紙": "カミ", "鉛筆": "エンピツ", "眼鏡": "メガネ", "卵": "タマゴ", "肉": "ニク", "米": "コメ",
                 "苺": "イチゴ", "蜜柑": "ミカン", "西瓜": "スイカ", "葡萄": "ブドウ", "桃": "モモ", "梨": "ナシ",
                 "柿": "カキ", "栗": "クリ", "寿司": "スシ", "蕎麦": "ソバ", "饂飩": "ウドン", "烏賊": "イカ",
                 "蛸": "タコ", "鮪": "マグロ", "狐": "キツネ", "狸": "タヌキ", "鼠": "ネズミ", "兎": "ウサギ",
                 "象": "ゾウ", "鶏": "ニワトリ", "雀": "スズメ", "烏": "カラス", "蝉": "セミ", "蟻": "アリ",
                 "歌": "ウタ", "絵": "エ", "夢": "ユメ", "窓": "マド", "扉": "トビラ", "鍵": "カギ", "箱": "ハコ"})
SMALL = str.maketrans("ぁぃぅぇぉゃゅょっゎゕゖ", "あいうえおやゆよつわかけ")


def reading(word):
    value = unicodedata.normalize("NFKC", READINGS.get(word, word))
    if not re.fullmatch(r"[ぁ-ゖァ-ヺー]+", value):
        return None
    value = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)
    value = value.translate(SMALL)
    # A terminal long mark continues from the preceding kana, as an explicit house rule.
    return value.rstrip("ー") or None


_KANJI_WORD_RE = re.compile(r"[一-龥々〆ヶぁ-ゖァ-ヺー]{1,10}")


def _kana_tail(word):
    """「感じ」 -> 「じ」: the kana a reading of the word has to end with."""
    tail = re.search(r"[ぁ-ゖァ-ヺー]*$", word)[0]
    return reading(tail) if tail else ""


def guess_reading(word, cfg):
    """The model writes one reading of a kanji word; code checks it is kana and fits the word (#169).
    Returns (reading as written, normalized reading) or None. The model never decides whether the word is allowed."""
    if not cfg or not _KANJI_WORD_RE.fullmatch(word) or not re.search(r"[一-龥々〆]", word):
        return None
    import llm
    system = ("Write the most common reading of the Japanese word below in katakana. "
              "Output only the katakana reading, nothing else.")
    try:
        out = llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": word}],
                       options={"temperature": 0, "num_predict": 20})
    except Exception:
        return None
    lines = [line.strip().strip("「」『』\"'。、 ") for line in (out or "").splitlines() if line.strip()]
    sound = reading(lines[0]) if lines else None
    if not sound or len(sound) > 3 * len(word) + 2:
        return None
    tail = _kana_tail(word)
    if tail and not sound.endswith(tail):
        return None  # 「感じ」 read as 「かんかく」 is not this word
    return lines[0], sound


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


def handle(text, cfg=None):
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
    guessed = None
    if not sound and not match:
        # Voice recognition writes most words in kanji (「虫」); the reading is said back so it can be corrected.
        guessed = guess_reading(literal, cfg)
        sound = guessed[1] if guessed else None
    if not sound:
        return "しりとり中だよ。一語だけ答えてね。漢字の言葉は「漢字（よみ）」の形で読みも教えて。終了するなら「しりとりをやめて」と言ってね。"
    said = f"「{word}（{guessed[0]}）」だね。" if guessed else ""
    if value["next"] and sound[0] != value["next"]:
        if guessed:
            return (f"「{word}」は「{guessed[0]}」と読んだけど、今は「{value['next']}」からだよ。"
                    f"読みが違ったら「{word}（よみ）」の形で教えてね。")
        return f"今は「{value['next']}」からだよ。もう一度どうぞ。"
    if sound in value["used"]:
        return f"{said}「{word}」はもう使ったよ。別の言葉をどうぞ。"
    if sound[-1] == "ん":
        save({})
        return f"{said}「{word}」は「ん」で終わるから、あなたの負け。しりとりは終了だよ。"
    value["used"].append(sound)
    value.update(last_word=word, next=sound[-1], turn="es")
    save(value)
    return said + _answer(value)
