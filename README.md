# fishing-crawler

네이버 카페의 특정 게시판을 로그인 세션으로 크롤링하는 파이썬 예제입니다.

현재 버전 기준 동작:

- 로그인 쿠키가 없으면 크롤링을 시작하지 않음
- 게시판 첫 페이지 게시글 목록 수집
- 각 게시글의 상세 API 호출
- 상세 API의 `isNotice` 기준으로 공지글 제외
- 게시판 규칙 또는 API 본문 기준으로 `어종`, `지역`, `장소` 분류
- 상세 접근 실패 상태도 함께 저장
- 결과를 `output/first_page.json`에 저장

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chrome chromium
```

## 로컬 개발용 Docker

로컬에서 적재 테스트를 하려면 Postgres와 OpenSearch를 먼저 띄우는 편이 편합니다.

```bash
docker compose up -d
```

기본 포트:

- Postgres: `localhost:5433`
- OpenSearch: `http://localhost:9200`
- OpenSearch Dashboards: `http://localhost:5601`

기본 계정/DB:

- Postgres DB: `fishing_crawler`
- Postgres user: `fishing`
- Postgres password: `fishing`

중지:

```bash
docker compose down
```

데이터까지 함께 삭제:

```bash
docker compose down -v
```

OpenSearch 확인 예시:

```bash
curl http://localhost:9200
```

Postgres 확인 예시:

```bash
docker compose exec postgres psql -U fishing -d fishing_crawler
```

Python 의존성도 한 번 더 맞춰주세요.

```bash
source .venv/bin/activate
pip install -e .
```

## 자동 로그인 테스트 준비

`.env.example`을 복사해 `.env`를 만든 뒤 네이버 계정 정보를 넣습니다.

```bash
cp .env.example .env
```

`.env`:

```text
NAVER_ID=여기에_네이버아이디
NAVER_PASSWORD=여기에_네이버비밀번호
```

`.env`는 `.gitignore`에 포함되어 있어서 Git에 올라가지 않습니다.

DB나 OpenSearch 연결값도 실제로 붙이기 시작하면 `.env`에 함께 넣으면 됩니다. 예를 들면:

```text
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=fishing_crawler
POSTGRES_USER=fishing
POSTGRES_PASSWORD=fishing
OPENSEARCH_HOST=http://localhost:9200
```

## 실행

```bash
python3 -m crawler.cli --board-key bass_walking --browser-channel chrome
```

자동 로그인만 먼저 테스트하고 싶으면:

```bash
python3 -m crawler.cli --test-login --browser-channel chrome
```

스토리지 연결 확인:

```bash
python3 -m crawler.cli --test-storage
```

Postgres 스키마와 OpenSearch 인덱스 초기화:

```bash
python3 -m crawler.cli --init-storage
```

첫 페이지를 크롤링하고 Postgres/OpenSearch에도 함께 넣기:

```bash
python3 -m crawler.cli \
  --board-key bass_walking \
  --browser-channel chrome \
  --headless \
  --store-postgres \
  --store-opensearch
```

기본 대상 게시판은 `bass_walking`이며 아래 게시판들을 미리 등록해뒀습니다.

- `bass_walking`: `배스 조행기(워킹조행)` / `https://cafe.naver.com/f-e/cafes/29836679/menus/3?viewType=L`
- `bass_boating`: `배스 조행기(보팅조행)` / `https://cafe.naver.com/f-e/cafes/29836679/menus/87`
- `freshwater_guest`: `민물 조행기(손님고기)` / `https://cafe.naver.com/f-e/cafes/29836679/menus/88`

다른 게시판으로 바꾸려면:

```bash
python3 -m crawler.cli \
  --board-key freshwater_guest
```

백필 모드 예시:

```bash
python3 -m crawler.cli \
  --mode backfill \
  --board-key bass_walking \
  --until-date 2026-01-01 \
  --page-size 50 \
  --browser-channel chrome \
  --headless
```

## 로그인 흐름

1. 스크립트가 브라우저를 엽니다.
2. `NID_SES` 또는 `NID_AUT` 로그인 쿠키가 없으면 로그인 페이지로 이동합니다.
3. `.env`에 `NAVER_ID`, `NAVER_PASSWORD`가 있으면 자동 로그인을 먼저 시도합니다.
4. 자동 로그인이 실패하면 사용자가 직접 로그인할 수 있습니다.
5. 로그인 쿠키가 확인되기 전까지는 게시글 크롤링을 시작하지 않습니다.
6. 로그인 후 게시판 목록에서 글 ID를 수집하고, 각 글의 상세 API를 호출합니다.
7. 백필 모드에서는 `page`와 `size`를 바꿔가며 목록을 순회하고, 작성일이 `until-date`보다 오래된 글이 나오면 멈춥니다.

로그인 세션은 `.playwright/naver-profile` 아래에 저장됩니다.

## 출력 구조

각 글은 대략 아래 형식으로 저장됩니다.

```json
[
  {
    "board_name": "배스 조행기(워킹조행)",
    "article_id": "123456789",
    "title": "남양주 배스 조행기",
    "url": "https://cafe.naver.com/f-e/cafes/29836679/articles/123456789",
    "author": "작성자",
    "date_text": "2026.03.30.",
    "category_label": "일반",
    "page_title": "남양주 배스 조행기 : 네이버 카페",
    "page_url": "https://cafe.naver.com/f-e/cafes/29836679/articles/123456789",
    "body_text": "오늘 남양주에서 배스...",
    "access": {
      "status": "ok",
      "reason": "상세 API에서 제목과 본문을 확인함",
      "page_title": "남양주 배스 조행기 : 네이버 카페",
      "page_url": "https://cafe.naver.com/f-e/cafes/29836679/articles/123456789",
      "visible_text_length": 2400,
      "explicit_public_signal": true,
      "explicit_public_reason": "상세 API의 article.isSearchOpen=true 로 외부 공개로 판단"
    },
    "classification": {
      "species": "배스",
      "species_reason": "게시판 규칙에 따라 어종을 '배스'로 고정",
      "external_open": null,
      "external_open_reason": "로그인 세션 기준 응답이라 외부 공개 여부는 현재 확정하지 않음",
      "region": "서울/경기권",
      "region_reason": "말머리 기준 분류: '서울' 키워드 기준으로 분류",
      "place": "남양주",
      "place_reason": "'장소/포인트' 패턴으로 '남양주' 추출"
    }
  }
]
```

## 저장 기준

- `classification.species`: `배스`, `블루길`, `숭어`, `가물치`, `강준치` 등 문자열
- `classification.external_open`: 현재는 항상 `null`
- `classification.region`: `서울/경기권`, `인천권`, `경상권`, `전라권`, `충청권`, `강원권`, `제주권`, `null`
- `classification.place`: `모월저수지`, `대호만`, `영암` 같은 구체 장소 문자열 또는 `null`
- `access.status`: `ok`, `member_only`, `login_required`, `deleted_or_missing`, `partial`, `unresolved`

운영 적재 시에는 보통 아래 기준이 안전합니다.

- 본문 인덱싱 대상: `access.status == "ok"`
- 외부공개는 현재 보수적으로 확정하지 않으므로 후속 검증 전까지는 필터로 사용하지 않는 편이 안전합니다.

## 참고

- 지금은 첫 페이지만 수집합니다.
- 백필 모드는 `https://cafe.naver.com/f-e/cafes/.../menus/{menuId}?page={n}&size={m}` 형식의 목록 URL을 사용합니다.
- `bass_walking`, `bass_boating` 게시판은 어종을 항상 `배스`로 고정합니다.
- `freshwater_guest` 게시판만 본문 기준 어종 분류를 사용합니다.
- 지역은 API의 `article.head`에서 먼저 찾고, 없으면 제목/본문에서 찾습니다.
- 장소는 본문에서 `장소 -`, `장소:`, `장소`, `포인트 -`, `포인트:` 패턴을 우선 추출합니다.
- 공지는 API의 `article.isNotice` 또는 실제 메뉴명이 `공 지 사 항`인 경우 제외합니다.
- 외부공개는 로그인 세션 영향 때문에 현재는 확정하지 않고 `null`로 저장합니다.
- 자동 로그인은 `.env` 기반의 1차 실험 버전입니다. 네이버 보안 정책에 따라 실패할 수 있습니다.
- 게시판 추가는 코드의 `BOARD_REGISTRY`에 규칙을 추가하면 됩니다.

## 로컬 스토리지 초기 구조

Postgres에는 아래 두 테이블을 둡니다.

- `articles`: 원본/정규화 게시글 저장용
- `crawl_checkpoints`: 게시판별 백필 재시작 지점 저장용

OpenSearch 기본 인덱스 이름은 `fishing_articles_v1` 입니다.
