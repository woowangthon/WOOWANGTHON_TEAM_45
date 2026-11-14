# 디자인 스킬 원본과 재설치

이번 작업에는 공식 저장소에서 받은 전체 스킬을 로컬 `.agents/skills/`에 설치해 사용했습니다. Git에는 요청된 세 `SKILL.md`를 보관합니다. 외부 스킬의 대규모 이미지·마케팅 템플릿·실행 바이너리는 앱 배포에 포함하지 않습니다. 새 작업 환경에서 참조 문서나 진단 도구가 필요하면 다음 원본의 전체 스킬을 다시 설치합니다.

| 스킬 | 공식 원본 | 라이선스 |
| --- | --- | --- |
| Emil Kowalski Design Engineering | https://github.com/emilkowalski/skills/tree/main/skills/emil-design-eng | MIT |
| Impeccable | https://github.com/pbakaus/impeccable/tree/main/.agents/skills/impeccable | Apache-2.0 |
| Taste Skill (1개) | https://github.com/Leonxlnx/taste-skill/tree/main/skills/taste-skill | MIT |

```sh
npx skills add emilkowalski/skills --skill emil-design-eng --agent codex
npx skills add pbakaus/impeccable --skill impeccable --agent codex
npx skills add Leonxlnx/taste-skill --skill design-taste-frontend --agent codex
```

설치된 지침은 다음 대화부터 자동 검색됩니다. 이번 작업에서는 설치 파일을 직접 읽어 적용했습니다. 기존 앱의 색상·기능 유지와 간결한 한국어 문구가 스킬의 일반 마케팅 페이지 규칙보다 우선합니다.

Node.js v25.9.0 확인. Playwright MCP는 로컬 Codex 설정에 다음 명령으로 등록했습니다(저장소를 받는 다른 개발자는 자신의 환경에서 별도 등록).

```sh
codex mcp add playwright -- npx @playwright/mcp@latest
codex mcp list
ls .agents/skills
```
