"""Fast source links and grounded Japanese sake product search.

Direct search links are free and immediate. AI-assisted search uses a separate
Gemini 2.5 Flash model so the main label-reading model/quota is not conflated.
It does not crawl, mirror, or bulk-copy retailer catalogues.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

SOURCE_REGISTRY: list[dict[str, str]] = [
    {"id": "katidoki", "name": "勝鬨酒販", "domain": "katidoki.shop", "url": "https://katidoki.shop/", "kind": "retailer"},
    {"id": "hasegawa", "name": "はせがわ酒店", "domain": "hasegawasaketen.com", "url": "https://www.hasegawasaketen.com/eshop/", "kind": "retailer"},
    {"id": "imadeya", "name": "いまでや酒店", "domain": "imadeya.co.jp", "url": "https://imadeya.co.jp/collections/nihonshu", "kind": "retailer"},
    {"id": "yamanaka", "name": "山中酒の店（大阪）", "domain": "yamanaka-sake.shop", "url": "https://www.yamanaka-sake.shop/", "kind": "retailer"},
    {"id": "sumiyoshi", "name": "住吉酒販", "domain": "field-to-table.jp", "url": "https://field-to-table.jp/", "kind": "retailer"},
    {"id": "nobori", "name": "登酒店", "domain": "nobori-sake.com", "url": "https://www.nobori-sake.com/", "kind": "retailer"},
    {"id": "sanoya", "name": "佐野屋 JIZAKE.COM", "domain": "jizake.com", "url": "https://www.jizake.com/", "kind": "retailer"},
    {"id": "kagiya", "name": "酒専門店鍵や", "domain": "sake-kagiya.com", "url": "https://www.sake-kagiya.com/", "kind": "retailer"},
    {"id": "yajima", "name": "矢島酒店", "domain": "yajima-jizake.co.jp", "url": "https://www.yajima-jizake.co.jp/", "kind": "retailer"},
    {"id": "matsuzaki", "name": "マツザキ", "domain": "matsuzaki-shop.jp", "url": "https://matsuzaki-shop.jp/", "kind": "retailer"},
    {"id": "sakenowa", "name": "さけのわ", "domain": "sakenowa.com", "url": "https://sakenowa.com/", "kind": "database"},
    {"id": "sakenowa_data", "name": "さけのわ公開データ", "domain": "muro.sakenowa.com", "url": "https://muro.sakenowa.com/sakenowa-data/", "kind": "open_data"},
    {"id": "sakeopendata", "name": "SAKEOpenData", "domain": "github.com/Code-for-SAKE/SAKEOpenData", "url": "https://github.com/Code-for-SAKE/SAKEOpenData", "kind": "open_data"},
    {"id": "sake_dataset", "name": "日本酒スペック公開データセット", "domain": "github.com/yoichi1484/sake_dataset", "url": "https://github.com/yoichi1484/sake_dataset", "kind": "open_data"},
    {"id": "brewery", "name": "酒蔵公式サイト", "domain": "", "url": "", "kind": "official"},
    {"id": "general", "name": "その他のウェブ検索", "domain": "", "url": "", "kind": "web"},
]


class ProductCandidate(BaseModel):
    """One candidate per distinct item / lot / bottling variation."""
    model_config = ConfigDict(extra="ignore")
    product_name: str = Field(description="実在ページ上で確認できる商品名。類似しているだけの名前は出さない")
    source_id: str = Field(description="SOURCE_REGISTRYのid。URLに一致する情報源を選ぶ")
    brewery: str | None = Field(default=None, description="蔵元名。不明ならnull")
    name_kana: str | None = Field(default=None, description="銘柄・商品名の読みをひらがなで。ページ等に根拠がなければnull")
    brewery_kana: str | None = Field(default=None, description="蔵元名の読みをひらがなで。根拠がなければnull")
    reading_basis: str | None = Field(default=None, description="読みの根拠についての短いメモ。未確認ならnull")
    source_url: str | None = Field(default=None, description="Google検索の出典として実際に参照された商品ページURL。未確認ならnull")
    spec_category: str | None = None
    processing_state: str | None = None
    by_year: str | None = None
    lot_date: str | None = None
    alcohol: float | None = None
    smv: float | None = None
    acidity: float | None = None
    amino_acid: float | None = None
    rice_polished: float | None = None
    rice_used: str | None = None
    yeast: str | None = None
    price_text: str | None = None
    availability: str | None = None
    brief_note: str | None = Field(default=None, description="日本語180文字以内の事実的な短い要約。商品説明を長文転載しない")
    fields_reported_by_ai: list[str] = Field(default_factory=list, description="ページに明記とAIが判断した項目名。アプリ上でも要確認扱い")


class ProductSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidates: list[ProductCandidate] = Field(default_factory=list)
    search_notes: list[str] = Field(default_factory=list)



def direct_source_search_url(query: str, source_id: str) -> str:
    """Build a fast, user-opened Google Search URL restricted to one source.

    This creates a search link only; it does not scrape the target site or assert
    that a result exists. The user opens and verifies the result in their browser.
    """
    cleaned = " ".join((query or "").split())
    source = next((item for item in SOURCE_REGISTRY if item["id"] == source_id), None)
    if source is None:
        raise ValueError("未知の情報源です。")
    if not cleaned:
        raise ValueError("検索語を入力してください。")
    domain = source.get("domain", "").strip()
    suffix = " 日本酒" if source.get("kind") in {"retailer", "official", "database", "open_data"} else ""
    if domain:
        search_text = f'site:{domain} "{cleaned}"{suffix}'
    elif source_id == "brewery":
        search_text = f'"{cleaned}" 日本酒 酒蔵 公式サイト 商品'
    elif source_id == "general":
        search_text = f'"{cleaned}" 日本酒'
    else:
        search_text = f'"{cleaned}" 日本酒 {source["name"]}'
    return "https://www.google.com/search?" + urlencode({"q": search_text, "num": "5"})

def selected_source_map(source_ids: list[str]) -> list[dict[str, str]]:
    wanted = set(source_ids)
    return [source for source in SOURCE_REGISTRY if source["id"] in wanted]


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname.lower().removeprefix("www.")
        port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
        path = parts.path.rstrip("/")
        # First compare the full URL, then canonical path without tracking query strings.
        return urlunsplit((parts.scheme.lower(), host + port, path, parts.query, ""))
    except (ValueError, AttributeError):
        return None


def _canonical_path_without_query(url: str | None) -> str | None:
    canon = canonical_url(url)
    if canon is None:
        return None
    parts = urlsplit(canon)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _source_matches_url(url: str | None, source: dict[str, str]) -> bool:
    if not url:
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return False
    domain = source.get("domain", "").lower().removeprefix("www.")
    if not domain:
        return source.get("id") in {"brewery", "general"}
    if "/" in domain:
        # Open data repositories are restricted to their exact GitHub repository path.
        domain_host, domain_path = domain.split("/", 1)
        expected_path = "/" + domain_path.strip("/").lower()
        actual_path = parts.path.rstrip("/").lower()
        return host == domain_host and (actual_path == expected_path or actual_path.startswith(expected_path + "/"))
    return host == domain or host.endswith("." + domain)


def _host_allowed(url: str | None, selected: list[dict[str, str]]) -> bool:
    return any(_source_matches_url(url, source) for source in selected)


def _source_for_url(
    url: str | None,
    selected: list[dict[str, str]],
    claimed_source_id: str | None = None,
) -> dict[str, str] | None:
    """Prefer exact configured domains; use generic sources only for unclassified URLs.

    Generic `brewery`/`general` entries intentionally have no domain. For those URLs,
    retain the model's source classification only when that generic source was selected.
    The URL still must match a Google grounding citation before the UI links it.
    """
    if not url:
        return None
    specific = [source for source in selected if source.get("domain")]
    match = next((source for source in specific if _source_matches_url(url, source)), None)
    if match:
        return match
    selected_by_id = {source["id"]: source for source in selected}
    if claimed_source_id in {"brewery", "general"} and claimed_source_id in selected_by_id:
        return selected_by_id[claimed_source_id]
    return None


def _interaction_text_and_citations(interaction: Any) -> tuple[str, dict[str, str]]:
    output_text = getattr(interaction, "output_text", None)
    if not output_text:
        raise ValueError("Geminiの検索結果本文が空でした。API権限・モデル名を確認してください。")
    citations: dict[str, str] = {}
    for step in getattr(interaction, "steps", []) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", []) or []:
            for annotation in getattr(block, "annotations", []) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                url = getattr(annotation, "url", None) or getattr(annotation, "uri", None)
                if not url:
                    continue
                title = getattr(annotation, "title", None) or url
                citations[str(url)] = str(title)
    return str(output_text), citations


SEARCH_TIMEOUT_MS = 15000
MAX_AI_SEARCH_SOURCES = 3
DEFAULT_SEARCH_MODEL = "gemini-2.5-flash"


def grounded_product_search(api_key: str, model: str, query: str, source_ids: list[str]) -> dict[str, Any]:
    """Run one bounded Google-grounded search call.

    ``model`` should be a model whose current free tier supports Search grounding
    (default in the app: gemini-2.5-flash). Calls use a short HTTP timeout and one
    attempt to avoid long waits on quota/rate-limit failures.
    """
    if not api_key:
        raise ValueError("Gemini APIキーが未設定です。")
    cleaned_query = " ".join((query or "").split())
    if not cleaned_query:
        raise ValueError("銘柄名または読み仮名を入力してください。")
    selected = selected_source_map(source_ids)
    if not selected:
        raise ValueError("検索する情報源を1つ以上選んでください。")
    if len(selected) > 3:
        raise ValueError("AI検索は速度と無料枠保護のため、1回に選べる情報源は3つまでです。")

    from google import genai
    from google.genai import types

    sources_text = "\n".join(
        f"- id={s['id']}; name={s['name']}; domain={s['domain'] or '一般検索'}; URL={s['url'] or '指定なし'}"
        for s in selected
    )
    prompt = f"""日本酒の商品情報を、指定された情報源に限定して短時間で検索してください。
検索語: {cleaned_query}
対象情報源:
{sources_text}

要件:
- 選択した情報源を優先し、検索を広げすぎないでください。
- 実在ページで確認できる商品候補を最大6件まで返してください。
- 商品名、蔵元名、読み仮名、スペックは出典ページに根拠がある場合だけ返し、不明値はnullにしてください。
- 同じ銘柄でも年度、ロット、火入れ/生、容量などが違うものは混ぜないでください。
- source_urlは実際に参照した商品・データページのみ。推測でURLを作らないでください。
- brief_noteは180文字以内。商品説明を長文転載しないでください。
- 見つからない項目はnull、候補がなければcandidates=[]にしてください。
- AIが推測した読みやスペックを確定情報として返さないでください。
"""

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=SEARCH_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    interaction = client.interactions.create(
        model=model,
        input=prompt,
        tools=[{"type": "google_search"}],
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": ProductSearchResponse.model_json_schema(),
        },
    )
    text, citation_map = _interaction_text_and_citations(interaction)
    parsed = ProductSearchResponse.model_validate_json(text)

    citation_exact: dict[str, str] = {}
    citation_paths: dict[str, str] = {}
    for url, title in citation_map.items():
        exact = canonical_url(url)
        path_only = _canonical_path_without_query(url)
        if exact:
            citation_exact[exact] = title
        if path_only:
            citation_paths[path_only] = title

    candidates: list[dict[str, Any]] = []
    for item in parsed.candidates[:6]:
        row = item.model_dump(mode="json")
        url = row.get("source_url")
        exact = canonical_url(url)
        path_only = _canonical_path_without_query(url)
        grounded = bool(exact and (exact in citation_exact or (path_only and path_only in citation_paths)))
        source_from_url = _source_for_url(url, selected, row.get("source_id"))
        if not grounded or not source_from_url:
            row["source_url"] = None
            row["url_verified_by_grounding"] = False
            row["source_id"] = "unverified"
            row["source_name"] = "出典未確認の候補"
        else:
            row["url_verified_by_grounding"] = True
            row["source_id"] = source_from_url["id"]
            row["source_name"] = source_from_url["name"]
        row["retrieved_at"] = None
        if row.get("brief_note"):
            row["brief_note"] = str(row["brief_note"])[:180]
        if row.get("name_kana"):
            from data_model import normalize_kana
            row["name_kana"] = normalize_kana(row["name_kana"])
        candidates.append(row)

    grounding_sources = []
    for url, title in citation_map.items():
        source = _source_for_url(url, selected)
        if source or any(item["id"] in {"brewery", "general"} for item in selected):
            grounding_sources.append({"title": title, "url": url, "source_name": source["name"] if source else "Google検索結果"})

    notes = list(parsed.search_notes)
    if not candidates:
        notes.append("候補が見つかりませんでした。下の情報源別リンクから直接検索するか、蔵元名・商品名を追加してください。")
    return {
        "query": cleaned_query,
        "selected_source_ids": source_ids,
        "candidates": candidates,
        "search_notes": notes,
        "grounding_sources": grounding_sources,
    }
