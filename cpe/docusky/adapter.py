"""Public DocuSky searches with source-specific original-text extraction.

The common read interface is documented by DocuWidgets and its linked SDK:
https://hackmd.io/@DocuSky/HJKNdaih4 . Only reviewed database profiles are exposed;
an OPEN catalog entry does not establish clean source text. Extraction evidence,
use conditions and coverage limits are in docs/research/docusky-connection.md.
"""

from __future__ import annotations

import http.client
import json
import re
import ssl
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.local.txt import LocalText

API_URL = "https://docusky.org.tw/DocuSky/WebApi/getQueryResultDocumentsJson.php"
CATALOG_URL = "https://docusky.org.tw/DocuSky/WebApi/getDbCorpusListJson.php"
PAGE_SIZE = 50
MAX_BYTES = 2 * 1024 * 1024
CATALOG_MAX_BYTES = 1 * 1024 * 1024
HOME_URL = "https://docusky.org.tw/"
SOURCE_PREFIX = "docusky:"
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
Transport = Callable[[str, dict[str, Any], float], bytes]


@dataclass(frozen=True)
class Profile:
    """A database this window may query.

    `reviewed` marks a database whose body layers were inspected. `inline` names the
    inline elements whose text belongs to the body; `notes` names elements that carry an
    original interlinear note, accepted only when their `Type` is in `note_types`. An
    element outside those sets excludes the whole record. An unreviewed database keeps
    only plain source paragraphs.
    """

    code: str
    database: str
    corpora: frozenset[str]
    project_url: str
    reviewed: bool = True
    inline: frozenset[str] = frozenset()
    notes: frozenset[str] = frozenset()
    note_types: frozenset[str] = frozenset()
    # A literal note span in the paragraph text, as observed in one database
    # (文獻通考經籍考 writes {{...}}). An unbalanced or nested span is handled by
    # flattening it into one note barrier; an unpaired marker rejects the record.
    markers: tuple[str, str] | None = None
    # A body element that separates units (本草經集注 writes <br/> between sections).
    # Each side becomes its own text, so no match crosses the break.
    breaks: frozenset[str] = frozenset()
    # Whether a newline in the paragraph text separates units (毛詩品物圖考).
    lines: bool = False
    # Whether root children are AlignBegin/AlignEnd pairs and the text between each
    # pair is one unit (春秋三傳).
    aligned: bool = False
    # Whether a Paragraph contains only Paragraph children, each of which is one unit
    # (欽定四庫全書總目提要).
    nested: bool = False
    # Root children that act as page breaks; the text after each is one unit
    # (DaoBudMed6D).
    root_breaks: frozenset[str] = frozenset()
    # Elements whose text is metadata, not body: skipped whole, tail kept.
    ignored: frozenset[str] = frozenset()
    # Accept a note element whatever its Type says (Comment, Note).
    notes_any_type: bool = False
    # Elements allowed inside a note (CommentItem).
    note_inline: frozenset[str] = frozenset()
    # Container elements whose text is body (section/div/p in 淡新檔案).
    containers: frozenset[str] = frozenset()
    # A record whose body is the root text itself, with no Paragraph.
    root_text: bool = False
    page_size: int = PAGE_SIZE


# Same interlinear-note markers the NIKH/ITKC importers write (spec 2.5); they do not
# occur in these releases.
NOTE_OPEN, NOTE_CLOSE = "〘", "〙"
# XML 1.0 cannot contain U+0000, so it separates units without colliding with text.
BREAK = "\u0000"
# Both the NIKH Sillok release and this DocuSky edition label an original note 원주.
ORIGINAL_NOTE = "원주"

TEMPLE = Profile(
    "temple-stories",
    "佛寺志靈應故事",
    frozenset({"普陀洛迦新志靈應故事", "清涼山志靈應故事"}),
    "https://docusky.org.tw/DocuSky/projects/ntu/BTGSRS/",
)
SILLOK = Profile(
    "sillok-online",
    "朝鮮王朝實錄",
    frozenset({"조선왕조실록朝鮮王朝實錄"}),
    HOME_URL,
    inline=frozenset({"PersonName", "LocName", "Udef_EraName"}),
    notes=frozenset({"Annotation"}),
    note_types=frozenset({ORIGINAL_NOTE}),
)
DAEMYEONG = Profile(
    "daemyeong-il-tong-ji",
    "大明一統志",
    frozenset({"大明一統志"}),
    HOME_URL,
    inline=frozenset(
        {
            "PersonName",
            "LocName",
            "Udef_Province",
            "Udef_City",
            "Udef_States",
            "Udef_County",
            "Udef_Mountain",
            "Udef_Monument",
            "Udef_Dynasty",
            "Udef_Palace",
            "Udef_Tomb",
            "Udef_Pass",
            "Udef_Temple",
            "Udef_Goods",
            "Udef_Office",
            "Udef_School",
            "Udef_Book",
            "Udef_Buddhism",
            "Udef_Garden",
            "Udef_Mausoleum",
            "Udef_Tan",
        }
    ),
    # A 50-record page of this database exceeds the 2 MiB response limit.
    page_size=5,
)
LUOYANG = Profile(
    "luoyang-qielan-ji",
    "洛陽伽藍記",
    frozenset({"洛陽伽藍記_公開庫"}),
    HOME_URL,
    inline=frozenset(
        {
            "Date",
            "PersonName",
            "Office",
            "LocName",
            "Udef_Construct",
            "Udef_Products",
            "Udef_King",
            "Udef_Governor",
            "Udef_General",
        }
    ),
)
WENXIAN = Profile(
    "wenxian-tongkao-jingjikao",
    "文獻通考經籍考",
    frozenset({"《文獻通考・經籍考》"}),
    HOME_URL,
    markers=("{{", "}}"),
)
BENCAO = Profile(
    "bencao-jing-jizhu",
    "本草經集注",
    frozenset({"本草經集注"}),
    HOME_URL,
    inline=frozenset(
        {
            "LocName",
            "PersonName",
            "span",
            "Udef_Actors",
            "Udef_DrugActions",
            "Udef_DrugAltName",
            "Udef_DrugEcology",
            "Udef_DrugInteraction",
            "Udef_DrugName",
            "Udef_DrugPreparation",
            "Udef_DrugProperty",
            "Udef_Harvesting",
            "Udef_MainDrugName",
            "Udef_MaterialPart",
            "Udef_RecipeName",
            "Udef_RelatedDrug",
            "Udef_SideEffect",
            "Udef_Taboo",
            "Udef_TextTitle",
            "Udef_notthisPlace",
            "Udef_originPlace",
            "Udef_relatedPlace",
        }
    ),
    breaks=frozenset({"br"}),
)
MAOSHI = Profile(
    "maoshi-pinwu-tukao",
    "毛詩品物圖考",
    frozenset({"《毛詩品物圖考》"}),
    HOME_URL,
    lines=True,
)
CHUNQIU = Profile(
    "chunqiu-sanzhuan",
    "春秋三傳",
    frozenset({"春秋", "春秋公羊傳", "春秋左氏傳", "春秋穀梁傳"}),
    HOME_URL,
    aligned=True,
)
JINMEN = Profile(
    "jinmen-documents",
    "金門傳統民間文書資料庫",
    frozenset({"金門傳統文書"}),
    HOME_URL,
    inline=frozenset(
        {
            "LocName",
            "PersonName",
            "Udef_Boundary",
            "Udef_Location",
            "Udef_Medium",
            "Udef_Object",
            "Udef_Owner",
            "Udef_Penman",
            "Udef_PersonNameLookbehind",
            "Udef_Source",
            "Udef_Taker",
            "Udef_Unit",
            "Udef_Witness",
        }
    ),
)
SIKU = Profile(
    "siku-quanshu-zongmu",
    "欽定四庫全書總目提要",
    frozenset({"欽定四庫全書總目提要"}),
    HOME_URL,
    nested=True,
    markers=("{{", "}}"),
)
ZAIYI = Profile(
    "zaiyi-records",
    "歷史自然災異記錄",
    frozenset({"歷史自然災異記錄"}),
    HOME_URL,
    inline=frozenset({"LocName", "Office", "Udef_DiHao", "Udef_ZaiYi", "Udef_date"}),
)
DAOBUD = Profile(
    "daobudmed6d",
    "DaoBudMed6D",
    frozenset({"Buddhist", "Daoist", "Medical"}),
    HOME_URL,
    root_breaks=frozenset({"Pb"}),
)
DANXIN = Profile(
    "danxin",
    "淡新檔案",
    frozenset({"淡新檔案"}),
    HOME_URL,
    containers=frozenset({"section", "div", "p"}),
    inline=frozenset(
        {
            "PersonName",
            "LocName",
            "Udef_Date",
            "Udef_HakkaIdentity",
            "Udef_HakkaTerm",
            "Udef_hakkaevent",
            "Udef_keywords",
        }
    ),
)
DANXIN_OPEN = Profile(
    "danxin-open",
    "淡新檔案公開庫",
    frozenset({"淡新檔案"}),
    HOME_URL,
    containers=frozenset({"section", "div", "p"}),
    inline=frozenset(
        {
            "PersonName",
            "LocName",
            "Udef_Date",
            "Udef_HakkaIdentity",
            "Udef_HakkaTerm",
            "Udef_hakkaevent",
            "Udef_keywords",
            "Udef_docclass",
        }
    ),
)
ZHOUHOU = Profile(
    "zhouhou",
    "ZhouHouFang",
    frozenset({"葛仙翁肘後備急方"}),
    HOME_URL,
    inline=frozenset(
        {
            "PersonName",
            "span",
            "Udef_BodyPart",
            "Udef_Diagnose",
            "Udef_DiseaseName",
            "Udef_DrugAction",
            "Udef_DrugAppearance",
            "Udef_DrugName",
            "Udef_DrugProcessing",
            "Udef_Harvesting",
            "Udef_Section_Headings",
        }
    ),
    breaks=frozenset({"br"}),
    notes=frozenset({"Comment"}),
    notes_any_type=True,
    note_inline=frozenset({"CommentItem", "span"}),
)
JIUHUANG = Profile(
    "jiuhuang",
    "救荒本草",
    frozenset({"救荒本草"}),
    HOME_URL,
    inline=frozenset(
        {
            "span",
            "Udef_BencaoName",
            "Udef_Book",
            "Udef_Cultivation",
            "Udef_Distribution",
            "Udef_Ecology",
            "Udef_EdiblePart",
            "Udef_FoodInfo",
            "Udef_InteractionItem",
            "Udef_Nature",
            "Udef_Origin",
            "Udef_Taste",
            "Udef_Toxicity",
            "Udef_Usage",
            "Udef_newName",
            "Udef_newPlace",
            "Udef_oldName",
            "Udef_oldPlace",
        }
    ),
    notes=frozenset({"Comment"}),
    notes_any_type=True,
    note_inline=frozenset({"CommentItem", "span"}),
)
CHENGZHENG = Profile(
    "chengzheng",
    "承政院日記",
    frozenset({"승정원일기"}),
    HOME_URL,
    inline=frozenset({"PersonName", "LocName"}),
    ignored=frozenset({"reference"}),
)
FOSIZHI = Profile(
    "fosizhi",
    "佛寺志人物傳記文本脈絡分析系統BTCAS_TGZ",
    frozenset({"@佛寺志書目TGZ_Bib", "佛寺志人物傳記TGZ_Biographies"}),
    HOME_URL,
    inline=frozenset(
        {
            "LocName",
            "PersonName",
            "Udef_NativePlace",
            "Udef_ref",
            "Udef_contentLN",
            "Udef_contentPN",
        }
    ),
)
WENXIANMULU = Profile(
    "wenxian-mulu",
    "文獻目錄資料庫",
    frozenset({"文獻目錄資料庫"}),
    HOME_URL,
    nested=True,
    markers=("{{", "}}"),
)
SIFUYIN = Profile(
    "sifuyin",
    "四福音-對讀",
    frozenset({"01馬太福音", "02馬可福音", "03路加福音", "04約翰福音"}),
    HOME_URL,
    aligned=True,
)
SIFUYIN_BILINGUAL = Profile(
    "sifuyin-bilingual",
    "四福音對讀中英文版",
    frozenset(
        {
            "01_Matthew",
            "01馬太福音",
            "02_Mark",
            "02馬可福音",
            "03_Luke",
            "03路加福音",
            "04_John",
            "04約翰福音",
        }
    ),
    HOME_URL,
    aligned=True,
)
HEHEBEN = Profile(
    "heheben",
    "和合本聖經",
    frozenset({"和合本聖經"}),
    HOME_URL,
    root_text=True,
    inline=frozenset({"PersonName", "LocName", "Udef_Ethnic", "Udef_Kings"}),
)
HEHEBEN_BILINGUAL = Profile(
    "heheben-bilingual",
    "和合本聖經-對讀",
    frozenset({"和合本聖經"}),
    HOME_URL,
    aligned=True,
    inline=frozenset({"PersonName", "LocName", "Udef_Ethnic", "Udef_Kings"}),
)
RELANZHE = Profile(
    "relanzhe",
    "熱蘭遮城日誌",
    frozenset({"第四冊"}),
    HOME_URL,
    inline=frozenset({"Udef_CT_AotoGen", "Udef_Footer"}),
)
MACKAY = Profile(
    "mackay",
    "Mackay",
    frozenset({"馬偕日記"}),
    HOME_URL,
    inline=frozenset(
        {
            "LocName",
            "PersonName",
            "SpecificTerm",
            "Udef_Bible",
            "Udef_Knowledge",
            "Udef_Plant",
            "Udef_Ship",
            "Udef_Vol_Ca",
        }
    ),
    notes=frozenset({"Comment"}),
    notes_any_type=True,
    note_inline=frozenset({"CommentItem", "span"}),
)
DRUGS = Profile(
    "drugs-asia",
    "DrugsAcrossAsia_China",
    frozenset({"DrugsAcrossAsia_China"}),
    HOME_URL,
)
YIWEN = Profile(
    "yiwen-leiju",
    "藝文類聚-太平御覽",
    frozenset({"太平御覽", "藝文類聚"}),
    HOME_URL,
    root_text=True,
    notes=frozenset({"Note"}),
    notes_any_type=True,
)
YAOFANG = Profile("yaofang", "藥方", frozenset({"藥方"}), HOME_URL, root_text=True)
GUIZHITANG = Profile(
    "guizhitang",
    "guizhitang",
    frozenset({"DrugsAcrossAsia_China"}),
    HOME_URL,
    ignored=frozenset({"Annotation"}),
)
PROFILES = {
    profile.code: profile
    for profile in (
        TEMPLE,
        SILLOK,
        DAEMYEONG,
        LUOYANG,
        WENXIAN,
        BENCAO,
        MAOSHI,
        CHUNQIU,
        JINMEN,
        SIKU,
        ZAIYI,
        DAOBUD,
        DANXIN,
        DANXIN_OPEN,
        ZHOUHOU,
        JIUHUANG,
        CHENGZHENG,
        FOSIZHI,
        WENXIANMULU,
        SIFUYIN,
        SIFUYIN_BILINGUAL,
        HEHEBEN,
        HEHEBEN_BILINGUAL,
        RELANZHE,
        MACKAY,
        DRUGS,
        YIWEN,
        YAOFANG,
        GUIZHITANG,
    )
}
BY_DATABASE = {profile.database: profile for profile in PROFILES.values()}


def open_profile(database: str) -> Profile:
    """An unreviewed public database, addressed by its catalogue name."""
    return Profile(
        f"open:{database}",
        database,
        frozenset(),
        HOME_URL,
        reviewed=False,
    )


def valid_database_name(name: str) -> bool:
    """A catalogue name: no surrounding space, bounded, no control characters."""
    return (
        bool(name)
        and name == name.strip()
        and len(name) <= 200
        and not any(unicodedata.category(char) == "Cc" for char in name)
    )


def profile_for_source(source: str) -> Profile:
    """The window's source code names a reviewed profile or a catalogue database."""
    if source == "docusky":
        return TEMPLE
    if source.startswith(SOURCE_PREFIX):
        database = source[len(SOURCE_PREFIX) :]
        if database in BY_DATABASE:
            return BY_DATABASE[database]
        if database:
            return open_profile(database)
    raise DocuskyError("unsupported database")


def known_profile(profile: Profile) -> bool:
    if profile.reviewed:
        return profile == PROFILES.get(profile.code)
    return valid_database_name(profile.database)


class DocuskyError(RuntimeError):
    """A failed public read or an invalid provider response."""


class DocuskyCapReached(RuntimeError):
    """The session request budget has been spent."""


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # This provider's chain lacks an SKI. Keep trust-chain and hostname checks;
    # Python 3.13+'s extra strict profile otherwise rejects the public endpoint.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": AGENT}
    )
    with urllib.request.urlopen(
        request, timeout=timeout, context=tls_context()
    ) as response:
        data: bytes = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise DocuskyError("response byte limit exceeded")
    return data


def literal_phrase(phrase: str) -> bool:
    """Do not let DocuSky interpret a locally literal term as query operators."""
    return 0 < len(phrase) <= 200 and all(
        unicodedata.category(char)[0] in "LN" for char in phrase
    )


@dataclass
class DocuskyClient:
    """Serialized reads: 1 s spacing, 200 attempts/session, no automatic retries."""

    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = 200
    timeout: float = 35.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def _send(
        self,
        url: str,
        params: dict[str, Any],
        checkpoint: Callable[[], None] | None,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """One serialized request: 1 s spacing, capped attempts, no retries."""
        bound = MAX_BYTES if limit is None else limit
        with self._lock:
            if checkpoint:
                checkpoint()
            if self.calls >= self.max_calls:
                raise DocuskyCapReached("session request cap reached")
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            if checkpoint:
                checkpoint()
            self._last = self.clock()
            self.calls += 1
            try:
                raw = self.transport(url, params, self.timeout)
                if len(raw) > bound:
                    raise DocuskyError("response byte limit exceeded")
                response = json.loads(raw)
            except (OSError, http.client.HTTPException, ValueError) as error:
                raise DocuskyError(str(error)) from None
            if checkpoint:
                checkpoint()
        if (
            not isinstance(response, dict)
            or type(response.get("code")) is not int
            or response["code"] != 0
        ):
            raise DocuskyError("public query failed")
        return response

    def search(
        self,
        phrase: str,
        profile: Profile = TEMPLE,
        *,
        page_size: int = PAGE_SIZE,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not known_profile(profile) or not literal_phrase(phrase):
            raise DocuskyError("unsupported database or query")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= PAGE_SIZE
        ):
            raise DocuskyError("invalid page size")
        params = {
            "target": "OPEN",
            "db": profile.database,
            "corpus": "[ALL]",
            "query": phrase,
            "page": 1,
            "pageSize": page_size,
            "requester": "classic-pattern-explorer",
        }
        response = self._send(API_URL, params, checkpoint)
        message = response.get("message")
        if not isinstance(message, dict):
            raise DocuskyError("query message must be an object")
        if (
            message.get("target") != "OPEN"
            or message.get("db") != profile.database
            or message.get("corpus") != "[ALL]"
            or message.get("query") != phrase
            or type(message.get("page")) is not int
            or type(message.get("pageSize")) is not int
            or message.get("page") != 1
            or message.get("pageSize") != page_size
        ):
            raise DocuskyError("response query scope does not match the request")
        total = message.get("totalFound")
        docs = message.get("docList")
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or not isinstance(docs, list)
            or len(docs) > page_size
            or len(docs) > total
        ):
            raise DocuskyError("invalid query result counts")
        return message

    def catalog(
        self, checkpoint: Callable[[], None] | None = None
    ) -> list[CatalogEntry]:
        """The provider's public database/corpus list (the window's profile choices)."""
        params = {"target": "OPEN", "requester": "classic-pattern-explorer"}
        response = self._send(CATALOG_URL, params, checkpoint, limit=CATALOG_MAX_BYTES)
        rows = response.get("message")
        if not isinstance(rows, list):
            raise DocuskyError("catalog must be a list")
        corpora: dict[str, set[str]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("target") != "OPEN":
                raise DocuskyError("invalid catalog row")
            database, corpus = row.get("db"), row.get("corpus")
            if (
                not isinstance(database, str)
                or not isinstance(corpus, str)
                or not valid_database_name(database)
                or not corpus.strip()
            ):
                raise DocuskyError("invalid catalog identity")
            corpora.setdefault(database, set()).add(corpus)
        return [
            CatalogEntry(database, tuple(sorted(names)))
            for database, names in sorted(corpora.items())
        ]


def _xml(value: str) -> ET.Element | None:
    if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
        return None
    try:
        return ET.fromstring(
            value,
            parser=ET.XMLParser(
                target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
            ),
        )
    except (ET.ParseError, ValueError):
        return None


def _inline_children(
    element: ET.Element, profile: Profile, allowed: frozenset[str] | None = None
) -> str | None:
    """Text of an allowed inline element; an unknown child rejects the record."""
    permitted = profile.inline if allowed is None else allowed
    parts = [element.text or ""]
    for child in element:
        if child.tag not in permitted:
            return None
        inner = _inline_children(child, profile, permitted)
        if inner is None:
            return None
        parts.append(inner)
        parts.append(child.tail or "")
    return "".join(parts)


def _inline_text(element: ET.Element, profile: Profile) -> str | None:
    parts = [element.text or ""]
    for child in element:
        if child.tag in profile.ignored:
            pass
        elif child.tag in profile.breaks:
            parts.append(BREAK)
        elif child.tag in profile.notes:
            kind = child.get("Type") or child.get("type")
            if not profile.notes_any_type and kind not in profile.note_types:
                return None
            inner = _inline_children(
                child, profile, profile.note_inline or profile.inline
            )
            if inner is None:
                return None
            parts.append(NOTE_OPEN + inner.strip() + NOTE_CLOSE)
        elif child.tag in profile.inline:
            inner = _inline_children(child, profile)
            if inner is None:
                return None
            parts.append(inner)
        else:
            return None
        parts.append(child.tail or "")
    return "".join(parts)


def _apply_markers(text: str, profile: Profile) -> str | None:
    """Turn a database's literal note spans into the shared note barrier.

    Nested spans are flattened into one note: everything between the outermost pair is
    note text. An unpaired marker, or text that already contains the barrier characters,
    rejects the record instead of guessing where the note ends.
    """
    if profile.markers is None:
        return text
    if NOTE_OPEN in text or NOTE_CLOSE in text:
        return None
    opened, closed = profile.markers
    depth = 0
    result: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith(opened, index):
            if depth == 0:
                result.append(NOTE_OPEN)
            depth += 1
            index += len(opened)
        elif text.startswith(closed, index):
            if depth == 0:
                return None
            depth -= 1
            if depth == 0:
                result.append(NOTE_CLOSE)
            index += len(closed)
        else:
            result.append(text[index])
            index += 1
    if depth:
        return None
    return "".join(result)


# Inline elements an unreviewed root-level body may carry. They wrap source characters;
# a name outside this set, or a nested element, rejects the record.
OPEN_ROOT_INLINE = frozenset({"PersonName", "LocName", "OfficerName", "SpecificTerm"})


def _open_root_text(root: ET.Element) -> list[str]:
    """A root-level body as one unit; the record stays labelled unreviewed."""
    parts = [root.text or ""]
    for child in root:
        if child.tag not in OPEN_ROOT_INLINE or len(child):
            return []
        parts.append("".join(child.itertext()))
        parts.append(child.tail or "")
    text = "".join(parts).strip()
    return [text] if text else []


def _nested_paragraphs(root: ET.Element, profile: Profile) -> list[str]:
    """A container Paragraph whose Paragraph children are the units."""
    if (root.text or "").strip():
        return []
    paragraphs: list[str] = []
    for child in root:
        if (child.tail or "").strip():
            return []
        if child.tag == "MetaTags":
            continue
        if child.tag != "Paragraph" or (child.text or "").strip():
            return []
        for inner in child:
            if inner.tag != "Paragraph" or len(inner) or (inner.tail or "").strip():
                return []
            text = _apply_markers(inner.text or "", profile)
            if text is None:
                return []
            for unit in text.split(BREAK):
                if unit.strip():
                    paragraphs.append(unit)
    return paragraphs


def _root_segments(root: ET.Element, profile: Profile) -> list[str]:
    """Root-level break elements; the text after each break is one unit."""
    if (root.text or "").strip():
        return []
    segments: list[str] = []
    for child in root:
        if child.tag not in profile.root_breaks or len(child):
            return []
        text = (child.tail or "").strip()
        if text:
            segments.append(text)
    return segments


def _aligned_paragraphs(root: ET.Element, profile: Profile = TEMPLE) -> list[str]:
    """AlignBegin/AlignEnd pairs; the text between a pair is one unit."""
    if (root.text or "").strip():
        return []
    paragraphs: list[str] = []
    inside = False
    parts: list[str] = []
    for child in root:
        if child.tag == "AlignBegin":
            if inside or len(child):
                return []
            inside = True
            parts = [child.tail or ""]
        elif child.tag == "AlignEnd":
            if not inside or len(child) or (child.tail or "").strip():
                return []
            inside = False
            text = "".join(parts).strip()
            if text:
                paragraphs.append(text)
            parts = []
        elif child.tag in profile.inline:
            if not inside:
                return []
            inner = _inline_children(child, profile)
            if inner is None:
                return []
            parts.append(inner)
            parts.append(child.tail or "")
        else:
            return []
    if inside:
        return []
    return paragraphs


def _units(text: str, profile: Profile) -> list[str]:
    units: list[str] = []
    for piece in text.split(BREAK):
        units.extend(piece.split("\n") if profile.lines else [piece])
    return [unit.strip() if profile.lines else unit for unit in units if unit.strip()]


def _container_paragraphs(root: ET.Element, profile: Profile) -> list[str]:
    """Text held by nested container elements (section/div/p in 淡新檔案)."""
    if (root.text or "").strip():
        return []

    def walk(element: ET.Element) -> str | None:
        parts = [element.text or ""]
        for child in element:
            if child.tag in profile.containers:
                inner = walk(child)
            elif child.tag in profile.inline:
                inner = _inline_children(child, profile)
            elif child.tag == "MetaTags":
                inner = ""
            else:
                return None
            if inner is None:
                return None
            parts.append(inner)
            parts.append(child.tail or "")
        return "".join(parts)

    paragraphs: list[str] = []
    for child in root:
        if child.tag == "MetaTags":
            continue
        if child.tag not in profile.containers:
            return []
        text = walk(child)
        if text is None:
            return []
        if text.strip():
            paragraphs.append(text.strip())
    return paragraphs


def profile_paragraphs(value: str, profile: Profile = TEMPLE) -> list[str]:
    """Source paragraphs under the allow-list; anything else rejects the record."""
    root = _xml(value)
    if root is None or root.tag != "Content":
        return []
    if profile.containers:
        return _container_paragraphs(root, profile)
    if profile.aligned:
        return _aligned_paragraphs(root, profile)
    if profile.nested:
        return _nested_paragraphs(root, profile)
    if profile.root_breaks:
        return _root_segments(root, profile)
    if (root.text or "").strip():
        if not profile.reviewed:
            return _open_root_text(root)
        if not profile.root_text:
            return []
        text = _inline_text(root, profile)
        if text is None:
            return []
        text = _apply_markers(text, profile)
        if text is None:
            return []
        return _units(text, profile)
    paragraphs: list[str] = []
    for child in root:
        if (child.tail or "").strip():
            return []
        if child.tag == "MetaTags":
            continue
        if child.tag != "Paragraph":
            return []
        text = _inline_text(child, profile)
        if text is None:
            return []
        text = _apply_markers(text, profile)
        if text is None:
            return []
        paragraphs.extend(_units(text, profile))
    return paragraphs


def original_paragraphs(value: str) -> list[str]:
    """Plain source Paragraphs only (the reviewed profile's rule)."""
    return profile_paragraphs(value, TEMPLE)


def _title(info: dict[str, Any]) -> str:
    title_xml = info.get("docTitleXml")
    title_root = _xml(title_xml) if isinstance(title_xml, str) else None
    return (
        title_root.text or ""
        if title_root is not None
        and title_root.tag == "DocTitle"
        and not len(title_root)
        else ""
    )


def result_texts(info: dict[str, Any], profile: Profile = TEMPLE) -> list[LocalText]:
    """Source paragraphs only; a reviewed profile also checks its own corpora.

    An unreviewed profile keeps the same structural rule (plain source Paragraphs,
    research tags skipped, anything else excluded whole). It does not establish that
    the database's layers are source text, so its result records say so in `edition`.
    """
    corpus = info.get("corpus")
    if not isinstance(corpus, str) or not corpus:
        return []
    if profile.reviewed:
        if (
            profile != PROFILES.get(profile.code)
            or corpus not in profile.corpora
            or info.get("xmlFormatName") != "ThdlContentXml"
        ):
            return []
    doc_id, filename = info.get("docId"), info.get("docFilename")
    if (
        not isinstance(doc_id, str)
        or not re.fullmatch(r"[0-9]+", doc_id)
        or not isinstance(filename, str)
        or not filename
    ):
        return []
    raw = info.get("docContentXml")
    if not isinstance(raw, str):
        return []
    paragraphs = profile_paragraphs(raw, profile)
    title = _title(info)
    layer = "" if profile.reviewed else " (unreviewed body layers)"
    return [
        LocalText(
            path=Path(),
            text_id=f"docusky:{profile.code}:{doc_id}#{index}",
            reference=text,
            range="excerpt",
            boundary_start="truncated",
            boundary_end="truncated",
            punctuation="modern",
            full_stop="sentence",
            text_type="original",
            title=f"{corpus} · {title.strip() or filename}",
            source=profile.project_url,
            note_open=NOTE_OPEN if profile.notes or profile.markers else None,
            note_close=NOTE_CLOSE if profile.notes or profile.markers else None,
            edition=(
                f"DocuSky {profile.database}{layer}; {corpus}; "
                f"{filename}; docId={doc_id}"
            ),
            sha256="",
            bom=False,
        )
        for index, text in enumerate(paragraphs, start=1)
    ]


@dataclass(frozen=True)
class CatalogEntry:
    """One public database from the provider's catalogue."""

    database: str
    corpora: tuple[str, ...]

    @property
    def source(self) -> str:
        return SOURCE_PREFIX + self.database

    @property
    def reviewed(self) -> bool:
        return self.database in BY_DATABASE


@dataclass(frozen=True)
class Fetched:
    total: int
    records: int
    accepted: int
    excluded: int
    texts: list[LocalText]


def fetch(
    phrase: str,
    client: DocuskyClient,
    profile: Profile = TEMPLE,
    *,
    page_size: int | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> Fetched:
    size = profile.page_size if page_size is None else page_size
    message = client.search(phrase, profile, page_size=size, checkpoint=checkpoint)
    docs = message["docList"]
    texts: list[LocalText] = []
    accepted = 0
    seen: set[str] = set()
    for doc in docs:
        if checkpoint:
            checkpoint()
        if not isinstance(doc, dict) or not isinstance(doc.get("docInfo"), dict):
            raise DocuskyError("document information must be an object")
        found = result_texts(doc["docInfo"], profile)
        if found:
            ids = {text.text_id for text in found}
            if ids & seen:
                raise DocuskyError("duplicate document identity")
            seen.update(ids)
            texts.extend(found)
            accepted += 1
    return Fetched(
        message["totalFound"], len(docs), accepted, len(docs) - accepted, texts
    )
