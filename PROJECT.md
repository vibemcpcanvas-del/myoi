# myoi: Project Master Architecture & Entrypoint (단일 진입점 SSOT)

> **단일 진입점 원칙 (Single Entry Point)**  
> 복잡한 환경 설정이나 수많은 스크립트를 직접 다룰 필요 없이, 사용자는 **오직 Antigravity 채팅에서 디렉팅과 의사결정에만 집중**합니다.  
> 모든 실행, 테스트, 검증, 상태 전이는 표준화된 거버넌스 규율과 Coarena Advisor 백엔드가 자동으로 보증합니다.

---

## 1. 역할 및 거버넌스 체계 (Roles & Governance Architecture)

| 엔티티 | 주체 | 핵심 책임 및 권한 |
| :--- | :--- | :--- |
| **디렉터 / 사용자 (Director)** | **User** | 프로젝트 방향성 기획, 최종 비즈니스 결정 승인, 예외 조항 최종 인가 |
| **실행 엔지니어 (Coordinator)** | **Antigravity Chat** | 코드 작성, WBS 패키지 구현, 테스트 작성, 산출물 생성 및 증빙 준비 |
| **수석 아키텍트 / 감사관 (Advisor)** | **Coarena API (`gpt-6-astra` 등)** | 사전 검수, 불변식 검증, 엄격한 2진 판정(`[PASS]` / `[REJECT]`), 게이트 차단 |

---

## 2. 진실 공급원 분리 원칙 (SSOT Separation Invariant)

1. **정책 및 절차의 SSOT**: 본 문서 ([`PROJECT.md`](file:///PROJECT.md))
2. **진행 상태 및 인수의 SSOT**: [`WBS.md`](file:///WBS.md)

---

## 3. 핵심 디렉터리 레이아웃 (Canonical Layout)

```text
myoi/
├── .agents/
│   ├── rules/                       # 거버넌스 및 실행 규율 4대 축
│   │   ├── 01-wbs-quota-governance.md
│   │   ├── 02-preflight-audit-gate.md
│   │   ├── 03-anti-fabrication-visual-integrity.md
│   │   └── 04-policy-traceability-matrix.md
│   └── hooks.json                   # Advisor 자동 감사 훅
├── .gitignore                       # 형상 관리 무시 목록
├── PROJECT.md                       # 시스템 아키텍처 및 진입점 SSOT
├── WBS.md                           # WBS 패키지 상태 원장 SSOT
├── specs/                           # WBS 패키지별 사양서 및 도메인 계약
├── tests/                           # 타겟 격리 단위/통합 테스트 스위트
├── docs/                            # 기술 문서 및 환경 구성 매뉴얼
└── scripts/                         # 마스터 러너 및 보조 자동화 도구
```
