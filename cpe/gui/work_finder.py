"""Curated catalog mapping classical works and collections to CPE sources.

The finder helps researchers answer "which database or official download holds
this work?" without running network calls or claiming exhaustive completeness.
Every human-readable title and scope note lives in the i18n catalogs under
``work_finder.*`` keys; entries here bind only verified source codes, optional
provider sub-sites (e.g. NIKH History DB eras), and verified ``portal.DATASETS``
primary keys.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OnlineMatch:
    """One verified online database entry matching a work or collection."""

    source: str
    note_key: str
    site: str = ""
    database: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {"source": self.source, "note_key": self.note_key}
        if self.site:
            data["site"] = self.site
        if self.database:
            data["database"] = self.database
        return data


@dataclass(frozen=True)
class WorkFinderEntry:
    """Curated guide entry linking a classical work to online DBs and releases."""

    work_id: str
    category_key: str
    title_key: str
    summary_key: str
    online: tuple[OnlineMatch, ...] = ()
    releases: tuple[str, ...] = ()
    external_imports: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.work_id,
            "category_key": self.category_key,
            "title_key": self.title_key,
            "summary_key": self.summary_key,
            "online": [item.to_dict() for item in self.online],
            "releases": list(self.releases),
            "external_imports": list(self.external_imports),
        }


CURATED_WORKS: tuple[WorkFinderEntry, ...] = (
    # --- Korean chronicles & histories ---
    WorkFinderEntry(
        work_id="samguk_sagi",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.samguk_sagi.title",
        summary_key="work_finder.work.samguk_sagi.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_ancient",
                site="ancient",
            ),
        ),
        releases=("15053635",),
    ),
    WorkFinderEntry(
        work_id="samguk_yusa",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.samguk_yusa.title",
        summary_key="work_finder.work.samguk_yusa.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_ancient",
                site="ancient",
            ),
        ),
        releases=("15053634",),
    ),
    WorkFinderEntry(
        work_id="haedong_goseungjeon",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.haedong_goseungjeon.title",
        summary_key="work_finder.work.haedong_goseungjeon.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_ancient",
                site="ancient",
            ),
        ),
        releases=("15053629",),
    ),
    WorkFinderEntry(
        work_id="goryeosa",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.goryeosa.title",
        summary_key="work_finder.work.goryeosa.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_goryeo",
                site="goryeo",
            ),
        ),
        releases=("15053637",),
    ),
    WorkFinderEntry(
        work_id="goryeosa_jeolyo",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.goryeosa_jeolyo.title",
        summary_key="work_finder.work.goryeosa_jeolyo.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_goryeo",
                site="goryeo",
            ),
        ),
        releases=("15115521",),
    ),
    WorkFinderEntry(
        work_id="joseon_sillok",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.joseon_sillok.title",
        summary_key="work_finder.work.joseon_sillok.summary",
        online=(
            OnlineMatch("sillok", "work_finder.match.sillok_direct"),
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_joseon",
                site="joseon",
            ),
            OnlineMatch(
                "docusky",
                "work_finder.match.docusky_sillok",
                database="朝鮮王朝實錄",
            ),
        ),
        releases=("15053647", "15053646"),
    ),
    WorkFinderEntry(
        work_id="seungjeongwon_ilgi",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.seungjeongwon_ilgi.title",
        summary_key="work_finder.work.seungjeongwon_ilgi.summary",
        online=(
            OnlineMatch(
                "docusky",
                "work_finder.match.docusky_seungjeongwon",
                database="承政院日記",
            ),
        ),
    ),
    WorkFinderEntry(
        work_id="ilseongnok",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.ilseongnok.title",
        summary_key="work_finder.work.ilseongnok.summary",
    ),
    WorkFinderEntry(
        work_id="gojongsidaesa",
        category_key="work_finder.category.korean_chronicles",
        title_key="work_finder.work.gojongsidaesa.title",
        summary_key="work_finder.work.gojongsidaesa.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_joseon",
                site="joseon",
            ),
        ),
        releases=("15138768",),
    ),
    # --- Korean registers, archives & wooden tablets ---
    WorkFinderEntry(
        work_id="bibyeonsa_deungnok",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.bibyeonsa_deungnok.title",
        summary_key="work_finder.work.bibyeonsa_deungnok.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_joseon",
                site="joseon",
            ),
            OnlineMatch("kyudb", "work_finder.match.kyudb_archives"),
        ),
        releases=("15053636",),
    ),
    WorkFinderEntry(
        work_id="gaksa_deungnok",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.gaksa_deungnok.title",
        summary_key="work_finder.work.gaksa_deungnok.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_joseon_modern",
                site="joseon",
            ),
        ),
        releases=("15053638", "15053639"),
    ),
    WorkFinderEntry(
        work_id="geumwiyeong_deungnok",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.geumwiyeong_deungnok.title",
        summary_key="work_finder.work.geumwiyeong_deungnok.summary",
        releases=("15052758",),
    ),
    WorkFinderEntry(
        work_id="sunmuyeong_deungnok",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.sunmuyeong_deungnok.title",
        summary_key="work_finder.work.sunmuyeong_deungnok.summary",
        releases=("15052764",),
    ),
    WorkFinderEntry(
        work_id="jangyongyeong_gosa",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.jangyongyeong_gosa.title",
        summary_key="work_finder.work.jangyongyeong_gosa.summary",
        external_imports=("release_local",),
    ),
    WorkFinderEntry(
        work_id="nrich_mokgan",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.nrich_mokgan.title",
        summary_key="work_finder.work.nrich_mokgan.summary",
        online=(OnlineMatch("nrich", "work_finder.match.nrich_tablets"),),
    ),
    WorkFinderEntry(
        work_id="aks_manuscripts",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.aks_manuscripts.title",
        summary_key="work_finder.work.aks_manuscripts.summary",
        online=(OnlineMatch("aksarchive", "work_finder.match.aks_manuscripts"),),
    ),
    WorkFinderEntry(
        work_id="kyujanggak_archives",
        category_key="work_finder.category.korean_registers",
        title_key="work_finder.work.kyujanggak_archives.title",
        summary_key="work_finder.work.kyujanggak_archives.summary",
        online=(OnlineMatch("kyudb", "work_finder.match.kyudb_archives"),),
    ),
    # --- Korean series & source compilations ---
    WorkFinderEntry(
        work_id="itkc_classical",
        category_key="work_finder.category.korean_series",
        title_key="work_finder.work.itkc_classical.title",
        summary_key="work_finder.work.itkc_classical.summary",
        online=(
            OnlineMatch("itkc", "work_finder.match.itkc_gojeon"),
            OnlineMatch("cyberseodang", "work_finder.match.cyberseodang_korean"),
        ),
    ),
    WorkFinderEntry(
        work_id="hanguk_munjip",
        category_key="work_finder.category.korean_series",
        title_key="work_finder.work.hanguk_munjip.title",
        summary_key="work_finder.work.hanguk_munjip.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_munjip"),),
    ),
    WorkFinderEntry(
        work_id="hanguk_saryo_chongseo",
        category_key="work_finder.category.korean_series",
        title_key="work_finder.work.hanguk_saryo_chongseo.title",
        summary_key="work_finder.work.hanguk_saryo_chongseo.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_joseon_modern",
                site="joseon",
            ),
        ),
        releases=("15053632",),
    ),
    WorkFinderEntry(
        work_id="hanguk_godae_saryo_jipseong",
        category_key="work_finder.category.korean_series",
        title_key="work_finder.work.hanguk_godae_saryo_jipseong.title",
        summary_key="work_finder.work.hanguk_godae_saryo_jipseong.summary",
        online=(
            OnlineMatch(
                "historydb",
                "work_finder.match.historydb_ancient",
                site="ancient",
            ),
        ),
        releases=("15053631",),
    ),
    WorkFinderEntry(
        work_id="dongmun_hwigo",
        category_key="work_finder.category.korean_series",
        title_key="work_finder.work.dongmun_hwigo.title",
        summary_key="work_finder.work.dongmun_hwigo.summary",
        online=(OnlineMatch("nahf", "work_finder.match.nahf_collections"),),
        external_imports=("release_local",),
    ),
    WorkFinderEntry(
        work_id="joseon_northern_history",
        category_key="work_finder.category.korean_series",
        title_key="work_finder.work.joseon_northern_history.title",
        summary_key="work_finder.work.joseon_northern_history.summary",
        online=(OnlineMatch("nahf", "work_finder.match.nahf_collections"),),
        external_imports=("release_local",),
    ),
    # --- Individual Korean miscellanies, geographies & collections ---
    WorkFinderEntry(
        work_id="sinjeung_dongguk_yeoji_seungnam",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.sinjeung_dongguk_yeoji_seungnam.title",
        summary_key="work_finder.work.sinjeung_dongguk_yeoji_seungnam.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15141472",),
    ),
    WorkFinderEntry(
        work_id="yeongnam_inmulgo",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.yeongnam_inmulgo.title",
        summary_key="work_finder.work.yeongnam_inmulgo.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15141469",),
    ),
    WorkFinderEntry(
        work_id="seja_haengjeok",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.seja_haengjeok.title",
        summary_key="work_finder.work.seja_haengjeok.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15141458",),
    ),
    WorkFinderEntry(
        work_id="goundang_pilgi",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.goundang_pilgi.title",
        summary_key="work_finder.work.goundang_pilgi.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15022432",),
    ),
    WorkFinderEntry(
        work_id="imha_pilgi",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.imha_pilgi.title",
        summary_key="work_finder.work.imha_pilgi.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15141448",),
    ),
    WorkFinderEntry(
        work_id="hoedangjip",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.hoedangjip.title",
        summary_key="work_finder.work.hoedangjip.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15096232",),
    ),
    WorkFinderEntry(
        work_id="nongsanjip",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.nongsanjip.title",
        summary_key="work_finder.work.nongsanjip.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15096009",),
    ),
    WorkFinderEntry(
        work_id="sabokjaejip",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.sabokjaejip.title",
        summary_key="work_finder.work.sabokjaejip.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15096008",),
    ),
    WorkFinderEntry(
        work_id="simseokjaejip",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.simseokjaejip.title",
        summary_key="work_finder.work.simseokjaejip.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15096001",),
    ),
    WorkFinderEntry(
        work_id="chongswae",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.chongswae.title",
        summary_key="work_finder.work.chongswae.summary",
        online=(OnlineMatch("itkc", "work_finder.match.itkc_search"),),
        releases=("15095996",),
    ),
    WorkFinderEntry(
        work_id="dongin_jimun_saryuk",
        category_key="work_finder.category.korean_individual_works",
        title_key="work_finder.work.dongin_jimun_saryuk.title",
        summary_key="work_finder.work.dongin_jimun_saryuk.summary",
        external_imports=("release_local",),
    ),
    # --- East Asian classics, epigraphy & history ---
    WorkFinderEntry(
        work_id="east_asian_classics_ctp",
        category_key="work_finder.category.east_asian_classics",
        title_key="work_finder.work.east_asian_classics_ctp.title",
        summary_key="work_finder.work.east_asian_classics_ctp.summary",
        online=(
            OnlineMatch("cyberseodang", "work_finder.match.cyberseodang_classics"),
        ),
        external_imports=("ctp",),
    ),
    WorkFinderEntry(
        work_id="nahf_historical_library",
        category_key="work_finder.category.east_asian_classics",
        title_key="work_finder.work.nahf_historical_library.title",
        summary_key="work_finder.work.nahf_historical_library.summary",
        online=(OnlineMatch("nahf", "work_finder.match.nahf_collections"),),
        external_imports=("release_local",),
    ),
    WorkFinderEntry(
        work_id="chinese_histories_dongyi_foreign",
        category_key="work_finder.category.east_asian_classics",
        title_key="work_finder.work.chinese_histories_dongyi_foreign.title",
        summary_key="work_finder.work.chinese_histories_dongyi_foreign.summary",
        online=(OnlineMatch("nahf", "work_finder.match.nahf_collections"),),
        external_imports=("release_local",),
    ),
    WorkFinderEntry(
        work_id="nahf_diplomatic_ancient_records",
        category_key="work_finder.category.east_asian_classics",
        title_key="work_finder.work.nahf_diplomatic_ancient_records.title",
        summary_key="work_finder.work.nahf_diplomatic_ancient_records.summary",
        online=(OnlineMatch("nahf", "work_finder.match.nahf_collections"),),
        external_imports=("release_local",),
    ),
    WorkFinderEntry(
        work_id="csid_stone_inscriptions",
        category_key="work_finder.category.east_asian_classics",
        title_key="work_finder.work.csid_stone_inscriptions.title",
        summary_key="work_finder.work.csid_stone_inscriptions.summary",
        online=(OnlineMatch("csid", "work_finder.match.csid_classics"),),
    ),
    # --- Chinese Buddhist canon & literature ---
    WorkFinderEntry(
        work_id="taisho_buddhist_canon",
        category_key="work_finder.category.buddhist_canons",
        title_key="work_finder.work.taisho_buddhist_canon.title",
        summary_key="work_finder.work.taisho_buddhist_canon.summary",
        online=(
            OnlineMatch("cbeta", "work_finder.match.cbeta_canon"),
            OnlineMatch("sat", "work_finder.match.sat_taisho"),
        ),
    ),
    WorkFinderEntry(
        work_id="docusky_buddhist_tales",
        category_key="work_finder.category.buddhist_canons",
        title_key="work_finder.work.docusky_buddhist_tales.title",
        summary_key="work_finder.work.docusky_buddhist_tales.summary",
        online=(
            OnlineMatch(
                "docusky",
                "work_finder.match.docusky_buddhist",
                database="佛寺志靈應故事",
            ),
        ),
    ),
    # --- Japanese, Taiwan & Vietnam sources ---
    WorkFinderEntry(
        work_id="engishiki",
        category_key="work_finder.category.japanese_taiwan_vietnam",
        title_key="work_finder.work.engishiki.title",
        summary_key="work_finder.work.engishiki.summary",
        online=(OnlineMatch("engishiki", "work_finder.match.engishiki"),),
    ),
    WorkFinderEntry(
        work_id="japanese_historical_records",
        category_key="work_finder.category.japanese_taiwan_vietnam",
        title_key="work_finder.work.japanese_historical_records.title",
        summary_key="work_finder.work.japanese_historical_records.summary",
        online=(OnlineMatch("ships", "work_finder.match.ships_diaries"),),
    ),
    WorkFinderEntry(
        work_id="japanese_classical_books",
        category_key="work_finder.category.japanese_taiwan_vietnam",
        title_key="work_finder.work.japanese_classical_books.title",
        summary_key="work_finder.work.japanese_classical_books.summary",
        online=(
            OnlineMatch("ndl", "work_finder.match.ndl_ocr"),
            OnlineMatch("cand", "work_finder.match.cand_catalog"),
        ),
        external_imports=("ndl",),
    ),
    WorkFinderEntry(
        work_id="taiwan_historical_corpus",
        category_key="work_finder.category.japanese_taiwan_vietnam",
        title_key="work_finder.work.taiwan_historical_corpus.title",
        summary_key="work_finder.work.taiwan_historical_corpus.summary",
        online=(
            OnlineMatch("ith", "work_finder.match.ith_fulltext"),
            OnlineMatch("thdl", "work_finder.match.thdl_library"),
            OnlineMatch("docusky", "work_finder.match.docusky_catalog"),
        ),
    ),
    WorkFinderEntry(
        work_id="vietnam_hannom_corpus",
        category_key="work_finder.category.japanese_taiwan_vietnam",
        title_key="work_finder.work.vietnam_hannom_corpus.title",
        summary_key="work_finder.work.vietnam_hannom_corpus.summary",
        online=(OnlineMatch("vietdien", "work_finder.match.vietdien_text"),),
    ),
    WorkFinderEntry(
        work_id="nahf_japanese_ancient_sources",
        category_key="work_finder.category.japanese_taiwan_vietnam",
        title_key="work_finder.work.nahf_japanese_ancient_sources.title",
        summary_key="work_finder.work.nahf_japanese_ancient_sources.summary",
        online=(OnlineMatch("nahf", "work_finder.match.nahf_collections"),),
        external_imports=("release_local",),
    ),
)


def work_finder_catalog() -> list[dict[str, object]]:
    """Return the static, offline curated catalog mapping works to DBs."""
    return [entry.to_dict() for entry in CURATED_WORKS]
