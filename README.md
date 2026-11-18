# liquidity-meter — 유동성 측정기

미국 달러 유동성을 **관측 → 측정 → 판정 → 검증**의 한 흐름으로 다루는 프로젝트다. 수집부터 게시까지 GitHub Actions에서 돈다. 컴퓨터가 켜져 있을 필요가 없다.

이전의 세 저장소(`macro-liquidity-dashboard`, `liqdesk`, `liqdesk-page`)를 하나로 합쳐 새로 짓고 있다.

> 투자 조언이 아니다. 이 프로젝트에서 두 가지 검정을 모두 넘은 신호는 아직 없다(사전 기준 하나만 넘은 것이 암호화폐에 하나 있고, 후보로 다룬다). 주식에 대해서는 검증된 신호가 없다. 그래서 새 신호를 찾는 것보다 **판정을 고칠 수 없게 기록하고 나중에 채점하는 것**을 먼저 만든다.

## 지금 상태

| 단계 | 내용 | 상태 |
|---|---|---|
| P1a | 수집 계층: 12개 소스 원자료, 상태 브랜치, 사이트 게시 | 구현됨 |
| P1b | 측정 계층: 준비금·활성비중 신호, 이력 저장소, 모델·주간 판독 페이지 | 이력 저장소만 구현됨 |
| P2 | 실시간 검증 기록: 사전 고정 규약, 추가 전용 기록, 채점 | 봉인·체인·채점 구현됨. 규약은 초안이라 아직 봉인하지 않음 |
| P3 | 하나의 판단 화면, 기계가 읽는 판독 JSON | 첫 화면에 유동성 사분면(총량 × 방향) 구현됨. 판독 JSON·주차 보관은 예정 |
| P4 | 잠정치(일별 TGA·역레포)와 확정치 구분 | 예정 |
| P5 | 측정 범위 확대: 맥락 층 + 섀도 후보 신호 | 임시 모델 보드(규칙 17개의 이번 주 상태와 조건 점수)만 구현됨. 섀도 기록은 예정 |
| P6 | 연결·정리 | 예정 |

## 구조

```
catalog/series.json   단일 등록부. provider·FRED 계열 목록은 여기서 파생하거나 테스트로 묶는다
lm/collect/           관측: 수집 조율, FRED 클라이언트, 차트 이력, provider 12개
lm/store/             이력 저장소: 모델 입력 계열의 전체 이력과 개정 기록
lm/measure/           측정: 총량·방향·준비금 변화와 여섯 칸(사분면), 임시 모델 보드. 가격을 읽지 않는다
lm/ledger/            검증 기록: 한 번만 봉인, 해시체인, 봉인 뒤 첫 관측으로 채점
lm/render/            화면: 허용 목록 기반 사이트 패키징
site/                 정적 화면: 판단 화면(index), 관측(observatory), 전체 자료(sources)
docs/                 소스별 원천·한계 문서 (게시됨)
research/protocols/   사전 고정 규약과 등록부. 고정된 규약으로만 봉인할 수 있다
research/archive/     이전 연구 보고서 (봉인, 갱신 안 함)
research/studies/     이 저장소에서 한 연구. 설계·파생 통계·스크립트
tests/                오프라인 테스트
.github/workflows/    pipeline.yml(수집·게시) · ci.yml(테스트)
```

브랜치는 역할이 다르다.

- `main`: 코드만.
- `data`: 기계가 쓰는 상태. `live/`(체크포인트), `chart-history/`, `history/`(모델 입력 이력과 개정 기록), `public/`(게시되는 JSON: 관측 스냅숏, 차트 이력, 사분면 지도, 임시 모델 보드). 파이프라인이 `./data`로 체크아웃해 쓴다.

## 실행

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

수집을 직접 돌려 보려면 다음 순서다. FRED는 `FRED_API_KEY` 환경변수가 있어야 하고, 없으면 FRED만 실패로 기록된다.

```bash
.venv/bin/python -m lm.collect.cloud_feeds --output-root .run/live --checkpoint data/live --public-output data/public/live-data.json
```

```bash
.venv/bin/python -m lm.collect.chart_history --output-root .run/chart-history --live-root data/live --checkpoint data/chart-history --public-output data/public/chart-data.json
```

```bash
.venv/bin/python -m lm.measure.quadrant --history data/history --output data/public/quadrant.json
```

```bash
.venv/bin/python -m lm.measure.models --history data/history --runtime .private/history --output data/public/models.json
```

```bash
.venv/bin/python -m lm.render.package --public-dir data/public
```

`_site/`가 게시되는 사이트다.

## 운영 원칙

- **키와 연락처를 저장소에 쓰지 않는다.** `FRED_API_KEY`·`SEC_USER_AGENT`는 GitHub secret이다. 테스트가 개인 경로·비공개 링크·키 형태를 검사한다(`tests/test_hygiene.py`).
- **실패한 수집이 최신인 척하지 않는다.** 실패한 소스는 마지막 정상값과 그 수집시각을 `retained`로 유지한다.
- **`known_by`는 실제 수집시각이다.** 최초 발표시각이나 과거 빈티지를 인증하지 않는다.
- **갱신 주기를 과장하지 않는다.** 매시 23분·53분 예약이지만 GitHub가 실제로 돌려 주는 것은 하루 몇 차례다. 화면의 수집 시각이 기준이다.
- **게시는 허용 목록으로만.** `lm/render/public-files.json`에 이름이 없는 파일은 사이트에 나가지 않는다.
- **재배포가 금지된 계열은 저장하지 않는다.** S&P 500·나스닥·코인베이스 BTC는 FRED에 재배포 금지로 표기돼 있어, 매 실행 때 받아 계산에만 쓰고 공개 브랜치에는 내용 해시만 남긴다.

## 데이터 출처

FRED, PBoC, SEC, 기업 IR, DefiLlama, OKX, Farside, 채굴사 공시, NY Fed, ECB, FINRA, 미 재무부 TIC, BIS, OFR, S&P DJI. 소스별 범위와 한계는 `docs/`에 있다.

This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis.
