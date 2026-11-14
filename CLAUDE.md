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
- 기계가 쓰는 상태는 `data` 브랜치에만 둔다. `main`에 상태 파일을 커밋하지 않는다.
- 코드 변경 후에는 항상 테스트를 돌린다.

## 명령

```sh
python -m unittest discover -s tests -p 'test_*.py'      # 전체 테스트 (오프라인, 페이지 테스트는 Node 필요)
python -m lm.collect.cloud_feeds --output-root .run/live --checkpoint data/live --public-output data/public/live-data.json
python -m lm.collect.chart_history --output-root .run/chart-history --live-root data/live --checkpoint data/chart-history --public-output data/public/chart-data.json
python -m lm.render.package --public-dir data/public     # _site/ 생성
```
