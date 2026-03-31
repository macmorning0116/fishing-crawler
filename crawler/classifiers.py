from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from crawler.places import match_place


REGION_KEYWORDS = {
    "서울/경기권": [
        "서울",
        "경기",
        "김포",
        "파주",
        "고양",
        "양평",
        "가평",
        "남양주",
        "여주",
        "안성",
        "포천",
        "연천",
        "수원",
        "화성",
        "평택",
        "용인",
        "하남",
        "의정부",
        "일산",
        "한강",
        "팔당",
    ],
    "인천권": [
        "인천",
        "강화",
        "영종",
        "송도",
    ],
    "경상권": [
        "부산",
        "대구",
        "울산",
        "경북",
        "경남",
        "포항",
        "구미",
        "안동",
        "경주",
        "창원",
        "진주",
        "통영",
        "거제",
        "밀양",
    ],
    "전라권": [
        "광주",
        "전북",
        "전남",
        "군산",
        "전주",
        "익산",
        "정읍",
        "남원",
        "목포",
        "여수",
        "순천",
        "나주",
        "영암",
        "담양",
    ],
    "충청권": [
        "대전",
        "세종",
        "충북",
        "충남",
        "청주",
        "충주",
        "제천",
        "천안",
        "아산",
        "서산",
        "당진",
        "보령",
        "공주",
        "부여",
    ],
    "강원권": [
        "강원",
        "춘천",
        "원주",
        "강릉",
        "속초",
        "양양",
        "홍천",
        "화천",
        "소양강",
    ],
    "제주권": [
        "제주",
        "서귀포",
    ],
}

SPECIES_KEYWORDS = {
    "배스": ["배스", "베스", "블랙배스", "black bass", "largemouth", "large mouth"],
    "블루길": ["블루길", "bluegill"],
    "숭어": ["숭어"],
    "가물치": ["가물치"],
    "강준치": ["강준치"],
    "붕어": ["붕어"],
    "잉어": ["잉어"],
    "쏘가리": ["쏘가리"],
    "메기": ["메기"],
    "송어": ["송어"],
    "향어": ["향어"],
    "누치": ["누치"],
    "끄리": ["끄리"],
}

ACCESS_DENIED_PATTERNS = [
    "멤버에게만 공개된 게시글",
    "카페 멤버에게만 공개된 게시글",
    "이 글은 카페 멤버만 볼 수 있습니다",
    "카페 가입 후 읽을 수 있습니다",
    "권한이 없습니다",
    "열람 권한이 없습니다",
    "접근 권한이 없습니다",
]

LOGIN_REQUIRED_PATTERNS = [
    "로그인 후 이용해주세요",
    "로그인이 필요합니다",
    "security login",
    "아이디 또는 전화번호",
    "비밀번호",
]

DELETED_PATTERNS = [
    "삭제된 게시글",
    "존재하지 않는 게시글",
    "게시글이 삭제되었거나",
    "없는 게시글",
]

PUBLIC_POSITIVE_PATTERNS = [
    '"searchOpenYn":"Y"',
    '"searchopenyn":"y"',
    '"outSideAllow":true',
    '"outsideallow":true',
    '"publicArticle":true',
    '"publicarticle":true',
    '"openType":"PUBLIC"',
    '"opentype":"public"',
    "외부 공유 허용",
]

PUBLIC_NEGATIVE_PATTERNS = [
    '"searchOpenYn":"N"',
    '"searchopenyn":"n"',
    '"outSideAllow":false',
    '"outsideallow":false',
    '"publicArticle":false',
    '"publicarticle":false',
    '"openType":"CAFE_MEMBER"',
    '"opentype":"cafe_member"',
    "외부 공유 비허용",
    "멤버에게만 공개된 게시글",
    "카페 멤버에게만 공개된 게시글",
]


@dataclass
class DetailAccessResult:
    status: str
    reason: str
    page_title: str
    page_url: str
    visible_text_length: int
    explicit_public_signal: Optional[bool]
    explicit_public_reason: str


@dataclass
class ClassificationResult:
    species: Optional[str]
    species_reason: str
    external_open: Optional[bool]
    external_open_reason: str
    region: Optional[str]
    region_reason: str
    place: Optional[str]
    place_reason: str


@dataclass
class ArticleApiFlags:
    is_notice: bool
    is_search_open: Optional[bool]
    is_enable_external: Optional[bool]
    is_sharable: Optional[bool]
    is_readable: Optional[bool]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_title(text: str) -> str:
    return normalize_text(text)


def contains_keyword(text: str, keywords: Iterable[str]) -> Optional[str]:
    lowered = normalize_text(text).lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            return keyword
    return None


def detect_detail_access_from_api(
    *,
    title: str,
    body_text: str,
    page_url: str,
    flags: ArticleApiFlags,
) -> DetailAccessResult:
    normalized_title = normalize_text(title)
    normalized_body = normalize_text(body_text)

    if flags.is_notice:
        return DetailAccessResult(
            status="notice",
            reason="상세 API에서 공지글로 표시됨",
            page_title=title,
            page_url=page_url,
            visible_text_length=len(normalized_body),
            explicit_public_signal=False,
            explicit_public_reason="공지글은 수집 대상에서 제외",
        )

    if flags.is_readable is False:
        return DetailAccessResult(
            status="member_only",
            reason="상세 API에서 읽기 권한이 없다고 표시됨",
            page_title=title,
            page_url=page_url,
            visible_text_length=len(normalized_body),
            explicit_public_signal=False,
            explicit_public_reason="읽기 권한이 없어 외부 비공개로 판단",
        )

    if flags.is_search_open is True:
        explicit_signal = True
        explicit_reason = "상세 API의 article.isSearchOpen=true 로 외부 공개로 판단"
    elif flags.is_search_open is False:
        explicit_signal = False
        explicit_reason = "상세 API의 article.isSearchOpen=false 로 외부 비공개로 판단"
    elif flags.is_enable_external is True and flags.is_sharable is True:
        explicit_signal = None
        explicit_reason = "외부 기능은 허용되지만 검색 공개 여부는 확인되지 않아 미확정"
    else:
        explicit_signal = None
        explicit_reason = "외부 공개 여부를 확정할 API 신호를 찾지 못함"

    if not normalized_title and not normalized_body:
        return DetailAccessResult(
            status="unresolved",
            reason="상세 API 응답은 왔지만 제목과 본문이 비어 있음",
            page_title=title,
            page_url=page_url,
            visible_text_length=len(normalized_body),
            explicit_public_signal=explicit_signal,
            explicit_public_reason=explicit_reason,
        )

    if len(normalized_body) < 20:
        return DetailAccessResult(
            status="partial",
            reason="상세 API 본문 텍스트가 매우 짧아 정상 수집으로 보기 어려움",
            page_title=title,
            page_url=page_url,
            visible_text_length=len(normalized_body),
            explicit_public_signal=explicit_signal,
            explicit_public_reason=explicit_reason,
        )

    return DetailAccessResult(
        status="ok",
        reason="상세 API에서 제목과 본문을 확인함",
        page_title=title,
        page_url=page_url,
        visible_text_length=len(normalized_body),
        explicit_public_signal=explicit_signal,
        explicit_public_reason=explicit_reason,
    )


def detect_detail_access(page_title: str, page_text: str, page_html: str, page_url: str, body_text: str) -> DetailAccessResult:
    combined = "\n".join([page_title, page_text, page_html, page_url])

    deleted = contains_keyword(combined, DELETED_PATTERNS)
    if deleted:
        return DetailAccessResult(
            status="deleted_or_missing",
            reason=f"'{deleted}' 패턴으로 삭제/미존재 글로 판단",
            page_title=page_title,
            page_url=page_url,
            visible_text_length=len(normalize_text(page_text)),
            explicit_public_signal=False,
            explicit_public_reason=f"'{deleted}' 상태에서는 외부 공개 글로 볼 수 없음",
        )

    denied = contains_keyword(combined, ACCESS_DENIED_PATTERNS)
    if denied:
        return DetailAccessResult(
            status="member_only",
            reason=f"'{denied}' 패턴으로 회원 전용 또는 권한 부족으로 판단",
            page_title=page_title,
            page_url=page_url,
            visible_text_length=len(normalize_text(page_text)),
            explicit_public_signal=False,
            explicit_public_reason=f"'{denied}' 패턴으로 외부 비공개로 판단",
        )

    login_required = contains_keyword(combined, LOGIN_REQUIRED_PATTERNS)
    if login_required and "nid.naver.com" in page_url:
        return DetailAccessResult(
            status="login_required",
            reason=f"'{login_required}' 패턴으로 로그인 필요 상태로 판단",
            page_title=page_title,
            page_url=page_url,
            visible_text_length=len(normalize_text(page_text)),
            explicit_public_signal=None,
            explicit_public_reason="로그인 페이지로 이동되어 외부 공개 여부를 판단할 수 없음",
        )

    public_negative = contains_keyword(combined, PUBLIC_NEGATIVE_PATTERNS)
    if public_negative:
        explicit_signal = False
        explicit_reason = f"'{public_negative}' 패턴으로 외부 비공개로 판단"
    else:
        public_positive = contains_keyword(combined, PUBLIC_POSITIVE_PATTERNS)
        if public_positive:
            explicit_signal = True
            explicit_reason = f"'{public_positive}' 패턴으로 외부 공개로 판단"
        else:
            explicit_signal = None
            explicit_reason = "외부 공개 여부를 확정할 명시 신호를 찾지 못함"

    normalized_title = normalize_text(page_title)
    normalized_body = normalize_text(body_text)
    if normalized_title == "네이버 카페" and not normalized_body:
        return DetailAccessResult(
            status="unresolved",
            reason="상세 페이지 제목이 '네이버 카페'이고 본문이 비어 있어 본문 접근 실패로 판단",
            page_title=page_title,
            page_url=page_url,
            visible_text_length=len(normalize_text(page_text)),
            explicit_public_signal=explicit_signal,
            explicit_public_reason=explicit_reason,
        )

    if len(normalized_body) < 20:
        return DetailAccessResult(
            status="partial",
            reason="상세 페이지는 열렸지만 본문 텍스트가 매우 짧아 정상 수집으로 보기 어려움",
            page_title=page_title,
            page_url=page_url,
            visible_text_length=len(normalize_text(page_text)),
            explicit_public_signal=explicit_signal,
            explicit_public_reason=explicit_reason,
        )

    return DetailAccessResult(
        status="ok",
        reason="상세 페이지 제목과 본문 텍스트가 확인되어 본문 접근 성공으로 판단",
        page_title=page_title,
        page_url=page_url,
        visible_text_length=len(normalize_text(page_text)),
        explicit_public_signal=explicit_signal,
        explicit_public_reason=explicit_reason,
    )


def detect_species(text: str) -> tuple[Optional[str], str]:
    normalized = normalize_text(text)
    if not normalized:
        return None, "본문이 비어 있어 어종을 판단할 수 없음"

    for species, keywords in SPECIES_KEYWORDS.items():
        keyword = contains_keyword(normalized, keywords)
        if keyword:
            return species, f"'{keyword}' 키워드로 '{species}' 어종을 판정"

    return None, "등록된 어종 키워드를 찾지 못함"


def detect_region(text: str) -> tuple[Optional[str], str]:
    for region_name, keywords in REGION_KEYWORDS.items():
        keyword = contains_keyword(text, keywords)
        if keyword:
            return region_name, f"'{keyword}' 키워드 기준으로 분류"

    return None, "권역 키워드를 찾지 못함"


def detect_place(text: str, title: str = "") -> tuple[Optional[str], str]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    stop_markers = [
        "날씨",
        "채비",
        "이동",
        "시간",
        "수위",
        "조과",
        "일시",
        "로드",
        "릴",
        "라인",
        "미끼",
    ]

    def cleanup_place(value: str) -> str:
        cleaned = normalize_text(value)
        for marker in stop_markers:
            cleaned = re.split(rf"\b{re.escape(marker)}\b\s*[-:]?", cleaned, maxsplit=1)[0]
        cleaned = cleaned.strip(" -:|,./")
        return cleaned

    line_patterns = [
        r"^(?:장소|포인트|장소명)\s*[-:：]?\s*(.+)$",
    ]

    for line in lines:
        for pattern in line_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if not match:
                continue
            place = cleanup_place(match.group(1))
            if place:
                return place, f"'장소/포인트' 줄 패턴으로 '{place}' 추출"

    title_patterns = [
        r"feat\.\s*([^)]+)",
        r"\(([^)]+(?:저수지|수로|호|강|만|지))\)",
        r"([가-힣0-9]+(?:저수지|수로|호|강|만|지))",
    ]
    for pattern in title_patterns:
        match = re.search(pattern, title or "", re.IGNORECASE)
        if not match:
            continue
        place = cleanup_place(match.group(1))
        if place:
            return place, f"제목 패턴으로 '{place}' 추출"

    return None, "본문/제목에서 장소 패턴을 찾지 못함"


def classify_article(
    title: str,
    body_text: str,
    access_result: DetailAccessResult,
    category_label: str = "",
    fixed_species: Optional[str] = None,
) -> ClassificationResult:
    combined = "\n".join(part for part in [title, body_text] if part)
    region_source_text = "\n".join(part for part in [category_label, title, body_text] if part)

    if access_result.status != "ok":
        species = None
        species_reason = f"본문 접근 상태가 '{access_result.status}'라 어종을 확정하지 않음"
        region = None
        region_reason = f"본문 접근 상태가 '{access_result.status}'라 지역을 확정하지 않음"
        place = None
        place_reason = f"본문 접근 상태가 '{access_result.status}'라 장소를 확정하지 않음"
    else:
        if fixed_species:
            species = fixed_species
            species_reason = f"게시판 규칙에 따라 어종을 '{fixed_species}'로 고정"
        else:
            species, species_reason = detect_species(combined)

        raw_place, raw_place_reason = detect_place(body_text, title=title)
        place, place_region, place_reason = match_place(raw_place or "", title, body_text)

        category_region, category_region_reason = detect_region(category_label)
        if category_region:
            region = category_region
            region_reason = f"말머리 기준 분류: {category_region_reason}"
        elif place_region:
            region = place_region
            region_reason = f"장소 사전 기준 분류: '{place}' -> {place_region}"
        else:
            region, region_reason = detect_region(region_source_text)

        if raw_place and place is None:
            place_reason = f"{raw_place_reason}; 하지만 장소 사전 매칭이 없어 null 처리"

    return ClassificationResult(
        species=species,
        species_reason=species_reason,
        external_open=None,
        external_open_reason="로그인 세션 기준 응답이라 외부 공개 여부는 현재 확정하지 않음",
        region=region,
        region_reason=region_reason,
        place=place,
        place_reason=place_reason,
    )
