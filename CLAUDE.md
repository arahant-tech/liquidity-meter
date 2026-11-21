# liquidity-meter

유동성을 관측 → 측정 → 판정 → 검증의 한 흐름으로 다루는 공개 프로젝트. 구조와 단계는 `README.md`를 볼 것.

## 지켜야 할 원칙

- **공개 저장소다.** 키·연락처·개인 경로·비공개 링크를 파일·문서·커밋·로그 어디에도 쓰지 않는다. `FRED_API_KEY`는 환경변수(GitHub secret)로만 받는다.
- **수집이 실패하면 낡은 값을 최신인 것처럼 내보내지 않는다.** `retained` 표시와 원래 수집시각을 유지한다.
- **하드코딩한 결론을 문서에 남기지 않는다.** 수치를 바꾸면 그 수치를 인용한 문구도 같이 고친다.
- **p 값은 원값과 보정값을 함께 적는다.** 다중검정 보정을 통과하지 못한 것을 "검증됨"이라고 쓰지 않는다.
- **이미 기각된 가설을 조용히 다시 시험하지 않는다.** 다시 열 때는 이유와 규약을 먼저 적는다.
- **셸·워크플로 변경은 실제로 돌려서 확인한다.** 코드 리뷰만으로 넘기지 않는다.
- 한국어로 답한다. 노이즈와 시그널을 구분하고 과장하지 않는다.

## 작업 규칙

- provider나 FRED 계열을 더하거나 뺄 때는 `catalog/series.json`부터 고친다. 다른 목록은 여기서 파생하거나 `tests/test_catalog.py`가 일치를 검사한다.
- 사이트에 새 파일을 내보내려면 `lm/render/public-files.json`에 이름을 넣는다.
- 사이트의 머리·바닥·색은 `site/site.css`와 모든 화면의 같은 머리·바닥 마크업에만 둔다. 화면을 더하면 여섯 화면의 머리를 함께 고친다. 문서 링크는 `doc.html?p=경로`로 건다.
- 연구를 더하면 `research/index.json`에 올린다.
- 측정(`lm/measure/`)은 가격을 읽지 않는다. 수익률과의 관계는 `research/studies/`의 고정된 연구 결과로만 화면에 싣고, 증거 등급을 함께 표시한다.
- 기계가 쓰는 상태는 `data` 브랜치에만 둔다. `main`에 상태 파일을 커밋하지 않는다.
- **봉인된 기록은 고치지 않는다.** 신호를 바꾸려면 새 규약 id로 새 기록을 시작한다. 고정된 규약 문서를 수정하면 봉인이 거부된다.
- `ledger` 브랜치에는 파이프라인만 쓴다. 손으로 커밋하거나 강제 push 하지 않는다. `research/protocols/PROTOCOL_tilt_v1.md`는 고정됐으므로 오탈자도 고치지 않는다.
- 재배포가 금지된 가격 계열(`persist: false`)의 값을 저장소·`data` 브랜치·게시물에 쓰지 않는다. 수익률과 날짜만 쓴다.
- 코드 변경 후에는 항상 테스트를 돌린다.

## 명령

```sh
python -m unittest discover -s tests -p 'test_*.py'      # 전체 테스트 (오프라인, 페이지 테스트는 Node 필요)
python -m lm.collect.cloud_feeds --output-root .run/live --checkpoint data/live --public-output data/public/live-data.json
python -m lm.collect.chart_history --output-root .run/chart-history --live-root data/live --checkpoint data/chart-history --public-output data/public/chart-data.json
python -m lm.measure.quadrant --history data/history --output data/public/quadrant.json   # 사분면 지도
python -m lm.measure.models --history data/history --runtime .private/history --output data/public/models.json   # 임시 모델 보드
python -m lm.ledger verify --dir ledger --anchor data/public/ledger.json   # 검증 기록 점검 (ledger 브랜치를 ./ledger 로 받은 뒤)
python -m lm.render.package --public-dir data/public     # _site/ 생성
```
