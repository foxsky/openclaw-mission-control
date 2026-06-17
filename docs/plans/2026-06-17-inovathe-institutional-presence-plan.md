# InovaTHE Institutional Presence Plan

> **Status:** Draft for operator review (v3, hardened after multi-agent + Codex review). Do not unpause the linked Mission Control task until this plan is approved. **Execution is non-interactive:** once unpaused, the Supervisor agent runs end-to-end with no further questions — every decision needed to build is already resolved in this plan (see "Decided Assumptions"). The only human touchpoints are (a) approving/unpausing this plan and (b) the pre-deploy review handoff in Task 6. There is no one for the agent to ask mid-run, so the plan contains no questions — only decisions.
>
> **v2 changelog (2026-06-17):** Rewritten after a four-agent review — INPACTA + INOVATEC-JP reference analysis, a Mission Control frontend architecture audit, and an adversarial plan review. **Major change:** InovaTHE is now an *independent standalone project*, NOT built inside the OpenClaw Mission Control Next.js app. This removes the auth-boundary, global `lang`/metadata, and shared-CSS risks that dominated v1. Governance guardrails, the test strategy, and the IA were also hardened.
>
> **v3.2 changelog (2026-06-17):** Operator provided the deploy target — production host `root@192.168.2.62:~/inovathe` (static `out/` served from there). Recorded in Tech Stack, Decided Assumptions, and the handoff; deploy remains a post-approval step, not the implementing agent's job.
>
> **v3.1 changelog (2026-06-17):** Non-interactive-readiness pass (second Codex re-review). Replaced "Open Questions" with a **Decided Assumptions** table (every unknown resolved to a baked-in default); pinned the exact non-interactive scaffold command, target-directory behavior, and one-commit lifecycle; added the required **utility/accessibility strings** to the Copy deck; resolved the stale Editais conflict (single Transparência block, no separate section/nav/footer link); pinned a plain text-only wordmark; dropped the optional `/acessibilidade` page; made the axe pass optional (structural checklist is the gate); and fixed the misleading hero CTA (`Ver informações de contato`).
>
> **v3 changelog (2026-06-17):** Hardened after an independent Codex (gpt-5.5/xhigh) spec review. Added a single **Copy Source-of-Truth** deck (no invented copy); made the contact section **non-collecting** (LGPD liability for a body with no data controller); promoted **static export, `robots: noindex`, and an a11y/SEO baseline** to hard requirements; reframed the negative test as a **best-effort tripwire, not proof**; banned photographic/representational imagery and all data-collection/third-party scripts; softened partner/PMO wording; and replaced MC-specific cleanup steps with a single "stay inside the InovaTHE repo" rule.

**Mission Control task:** `773d486a-76fb-4c75-a748-0644086c2db5` (the MC Supervisor agent orchestrates the build; the deliverable is a separate repository, not a change to the MC app).

## Decisions Locked By Operator

1. **Architecture:** Independent standalone project. Do **not** modify the OpenClaw Mission Control frontend, its `/` homepage, `AuthProvider`, root layout, or `globals.css`.
2. **First-pass scope:** Tight and fully populated. Build `Início`, `O Instituto`, `Soluções` (service/program areas), and `Contato` with real provisional copy. Add a single provisional `Transparência` block (clearly labeled "em estruturação") that also covers editais — no separate Editais section or nav/footer link. Never fabricated.
3. **Positioning:** Civic-innovation + governance blend — INOVATEC-JP's project/innovation-led model combined with InPACTA's transparency/governance/LAI structure and accessibility posture.

**Goal:** Build the first InovaTHE internet presence: a credible Portuguese institutional homepage and a starter visual identity direction for a Teresina civic-innovation institute, without copying either reference site and without inventing unapproved institutional facts.

## Architecture (independent project)

InovaTHE lives in its **own repository**, scaffolded fresh. It shares nothing with the Mission Control codebase.

- **Repo / directory (decided):** `~/Workspace/Agent/inovathe-site`, new `git init`, no shared history with `openclaw-mission-control`.
- **No auth gate.** The whole site is public by default — there is no `AuthProvider`, no login wall, no protected routes. (This is the single biggest simplification vs v1, which tried to carve a public route out of MC's local-auth gate.)
- **Own `app/layout.tsx`** sets `lang="pt-BR"`, InovaTHE `<title>`/metadata, and a favicon. No collision with any other app.
- **Own stylesheet** scoped to this project only. No shared global CSS, so no blast radius into anything else.
- **Why not inside MC (recorded for posterity):** the v1 plan targeted MC's `/` homepage. Audit findings that killed that approach: in `AUTH_MODE=local` (MC's production mode) `/` is hard-gated by `AuthProvider`, so content edits alone never go public; MC's `lang`/`title`/metadata are global in a single root layout and would leak Portuguese branding into every operator route; `globals.css` lines 1–128 are shared by ~28 MC files; and repurposing `/` would delete the OpenClaw operator landing. A standalone project avoids all of this.

## Tech Stack (decided — no confirmation step)

> The Supervisor agent is **non-interactive** — there is no one to confirm with mid-run, so every choice is decided here. Scaffold directly with these values; do not pause to ask.

- **Framework:** Next.js 16 (App Router; current stable — `next@latest` is 16.2.x as of 2026-06-17), React, TypeScript
- **Package manager:** npm (commit `package-lock.json`)
- **Node:** 20.9+ required by Next 16 (`engines: { node: ">=20.9.0" }`); use Node 22 LTS
- **Styling:** Tailwind CSS (project-scoped; no shared design tokens)
- **Tests:** Vitest + Testing Library (jsdom)
- **Static export is a hard constraint, not just a capability.** Set `output: "export"` in `next.config.ts`; the build MUST produce `out/index.html`. Because the first pass is static + non-collecting, these are **banned**: Server Actions, route handlers reading `Request`, cookies/headers, `rewrites`/`redirects`/`headers` config, and default `next/image` optimization (use `images: { unoptimized: true }` or plain `<img>`).
- **Deploy target (known):** the production host is `root@192.168.2.62:~/inovathe` — the static `out/` is served from there. The implementing agent still does NOT deploy: it produces the `out/` build and stops at the Task 6 review handoff. Copying `out/` to `192.168.2.62:~/inovathe` (and dropping `noindex`) is a **post-approval** step (operator or a DevOps-agent task), never automatic.
- **Verification:** browser screenshot at 1440px and 390px

(Recorded rationale, not a decision to revisit: a tiny static institutional placeholder does not strictly *need* a React framework; plain HTML/CSS or Astro would also work. Next 16 is chosen because the Supervisor's programmer agents already work in the Next/React ecosystem. The stack is **decided** — the agent uses Next 16; it does not weigh alternatives.)

## Source References

- INPACTA: `https://inpacta.org.br/`
- INOVATEC-JP: `https://inovatecjp.com/`
- LAI scope reference: `https://www.gov.br/acessoainformacao/pt-br/assuntos/conheca-seu-direito/a-lei-de-acesso-a-informacao`
- Ouvidoria/legal reference: `https://www.gov.br/ouvidorias/pt-br/central-de-conteudos/legislacao`
- LGPD/ANPD reference: `https://www.gov.br/anpd/pt-br/centrais-de-conteudo/outros-documentos-e-publicacoes-institucionais/lgpd-en-lei-no-13-709-capa.pdf`

Use the two institutional sites as **information-architecture and positioning references only**. Do not copy their text, visual design, images, logos, project claims, or legal claims.

> **Reference identity flag (operator awareness):** `inpacta.org.br` is **"InPACTA — Instituto de Projetos Avançados para Cidades, Tecnologia e Administração," a public-governance consultancy based in Maringá/PR** — *not* the Mossoró/RN technology park. It is an excellent transparency/governance-IA reference but follows a governance-consultancy model, not an incubator/tech-park model. INOVATEC-JP (João Pessoa/PB) supplies the project-led innovation half.

## Reference IA Findings (what to reuse — structure only)

From InPACTA (governance/transparency credibility model):

- **4-cluster primary nav** that separates identity, offering, accountability, and procurement: `O Instituto` / `Serviços` / `Transparência` / `Contratações e Parcerias`, plus standalone `Notícias` + `Contato`.
- **`O Instituto` groups** Quem Somos + Estrutura (governing council) + Equipe + LAI — surfacing governance bodies and the LAI inside the identity menu is a strong trust pattern.
- **A real Transparência portal keyed to LAI**: fixed document categories (Institucional / Governança / Normativos / Contratos e Parcerias / Prestação de Contas / Documentos Oficiais), each a card list of dated PDFs, plus an explicit "Solicitar Informação (LAI)" channel and a conformidade/stat header.
- **Separate Editais/Contratações area** with per-process status + phase tracking and a published Regulamento.
- **Accessibility/compliance as first-class**: utility-bar text-size control (A- / A / A+), dark-mode toggle, dedicated `/acessibilidade` and `/lgpd` pages, LGPD consent on forms.

From INOVATEC-JP (project-led innovation model):

- **Project-led homepage**: lead with concrete flagship projects (each with a sponsor/partner) rather than abstract mission text.
- **Triple-helix positioning** ("bridge between academia, government, and companies") — a clean one-line institutional frame. (Note: do NOT reuse INOVATEC-JP's exact wording "ponte entre academia, governo" — see governance prohibitions below.)
- **Contact-reason taxonomy**: separate intents for service requests, edital questions, partnership proposals, "trabalhe conosco," and general feedback.

Anti-patterns to avoid (observed on the references):

- Do NOT gate Editais/Transparência behind login (INOVATEC-JP does — wrong for a civic body).
- Transparência must be a *functional* hub concept, not a values paragraph.
- Avoid Gmail-only contact, Instagram-only social, and leftover English template boilerplate ("All right reserved", "© 2023") that reads as abandoned.
- Distinguish a public-interest civic mission from a "Contrate nossos serviços / orçamento" digital-agency framing.

## Information Architecture (tight first pass)

Primary navigation — built and fully populated:

- `Início`
- `O Instituto`
- `Soluções` (areas of activity / program lines)
- `Contato`

Present but explicitly provisional — rendered as a **homepage block** reached via the in-page trust-shortcuts row, NOT a top-level nav or footer link in the first pass (so the mobile first menu stays to the four populated sections and provisional blocks aren't featured as if live):

- `Transparência` — a single provisional block describing what it *may* hold (copy in the deck below). Editais is **folded into this block** (its copy already references editais) — do NOT build a separate Editais section.

Deferred (do NOT build in the first pass; add only when there is real content and confirmed mandate):

- `Projetos e Iniciativas` (needs real, approved projects — none confirmed)
- `Notícias` (needs a real publishing cadence)
- `Parcerias` (needs an approved partnership instrument)

Homepage sequence (first pass):

1. Mission hero (positioning + provisional subhead)
2. Trust shortcuts row (links to the sections that exist: `O Instituto`, `Soluções`, `Transparência`, `Contato`)
3. `O Instituto` (condensed: purpose / aspiration / how it intends to act)
4. `Soluções` (areas of activity — framed as aspiration, not a service catalog)
5. `Transparência` (provisional placeholder block)
6. `Contato`

## Positioning & Content Direction

Position InovaTHE as a **civic-innovation institute** for Teresina that articulates technology, data intelligence, public-project support, governance practice, open ecosystem work, and institutional transparency — combining an innovation-project orientation with a governance/transparency posture.

Use safe provisional wording (these verbs are **mandatory** in any sentence that touches mandate, authority, services, partners, or outcomes):

- "atua para apoiar"
- "busca promover" / "busca articular"
- "em diálogo com atores públicos e sociais" (preferred over "em articulação com órgãos e parceiros", which implies existing partnerships)
- "quando houver arranjo institucional definido"
- "canal oficial a ser informado quando o responsável estiver definido"

Avoid unsupported claims:

- "órgão oficial", "autoridade responsável", "garante atendimento", "coordena a política pública"
- "parceiros oficiais" without an approved instrument
- CNPJ, legal status, document counts, contract values, phone numbers, addresses, deadlines, protocol numbers, team names, or project outcomes unless sourced and approved

The exact provisional copy for every section — including Transparência and the non-collecting Contato block — is specified once, verbatim, in **Copy Source-of-Truth** below. That deck is the single authoritative source; do not duplicate or re-draft section copy here or anywhere else.

## Content Governance (hardened — text AND visual)

These are enforced rules for autonomous execution. The institute has **no confirmed legal status, no published projects, no confirmed partners, addresses, or contacts.**

Text rules:

1. **The hero H1 may state purpose/positioning but must NOT claim official authority, legal status, or guaranteed outcomes.** The hero **subhead must carry a mandatory provisional verb** (busca / atua para apoiar / em diálogo com).
   - Safe default H1: `Inovação, dados e governança pública para Teresina.`
   - Safe default subhead: `O InovaTHE busca articular tecnologia, dados e boas práticas de gestão em apoio a iniciativas de inovação na cidade de Teresina, em diálogo com atores públicos e sociais.`
   - The v1 headline "...para transformar Teresina" is **rejected** (asserts a guaranteed outcome for a body of unknown mandate).
2. **Service/solution lines must be framed as intended areas of activity, not a delivered catalog.** Use a section lede like `Áreas em que o InovaTHE busca atuar:` — never present them as services the institute currently provides.
3. **Empty states must not presuppose entity legitimacy.** Instead of "Nenhum edital aberto no momento" (which implies InovaTHE is an entity that *can* run editais), use provisional framing such as: `Esta área será utilizada para divulgar editais e oportunidades quando o arranjo institucional estiver definido.` Same principle for Transparência.

Visual rules (NEW — v1 had no visual-fact governance):

4. **Any generated logo, wordmark, or favicon is a NON-BINDING design proposal**, never presented as the institute's official/approved brand. Label it as a proposal in the handoff.
5. **No partner, sponsor, or government logos of any kind** (real or generated) — InovaTHE has no approved partnership instrument.
6. **No photographic or representational imagery in the first pass.** No photos, AI-realistic scenes, people, buildings, offices, event audiences, signed documents, maps, government crests, or skyline stand-ins — any of these can read as fabricated facilities, staff, events, or official status. Allowed: typography, color, and simple non-representational/abstract graphics only; all decorative graphics use empty `alt=""`.

Data / privacy rules:

7. **No data collection and no third-party scripts in the first pass.** No contact form, input field, newsletter signup, file upload, `mailto:`/`tel:`/`wa.me` link, embedded map, social-profile link, comment widget, or analytics/tracking of any kind. For a body with no confirmed data controller or privacy notice, collecting anything is an LGPD liability. The site is a static, non-collecting institutional placeholder until the operator supplies an approved contact channel + controller + privacy notice. The "contact action" in the hero is an in-page anchor to the provisional Contato block, never a form.

## Copy Source-of-Truth (use VERBATIM)

Every visible string in the first pass comes from this deck. **The agent renders only these strings — it does not request, infer, or invent copy.** If an element would need text that isn't in this deck, omit that element rather than fabricate it. All copy is provisional and governance-safe: it asserts no legal status, authority, partners, contacts, or outcomes. Render exactly these strings (PT-BR).

**Metadata**

- `<title>`: `InovaTHE — Inovação e governança pública em Teresina`
- meta description: `Iniciativa de inovação cívica para Teresina, com foco em tecnologia, dados e boas práticas de gestão pública. Site institucional em estruturação.`
- `lang`: `pt-BR` · `robots`: `noindex` (until the operator approves public launch)

**Primary nav:** `Início` · `O Instituto` · `Soluções` · `Contato`

**Hero**

- H1: `Inovação, dados e governança pública para Teresina.`
- Subhead: `O InovaTHE busca articular tecnologia, dados e boas práticas de gestão em apoio a iniciativas de inovação na cidade de Teresina, em diálogo com atores públicos e sociais.`
- Primary CTA: `Conheça o Instituto` → `#o-instituto`
- Secondary CTA: `Ver informações de contato` → `#contato` (in-page anchor to the provisional Contato block, NOT a form or mailto; the block states the official channel is not yet defined, so the label must not promise a conversation)

**Trust shortcuts row:** `O Instituto` · `Soluções` · `Transparência` · `Contato` (all in-page anchors)

**O Instituto**

- Heading: `O Instituto`
- Body: `O InovaTHE é uma iniciativa voltada à inovação cívica em Teresina. Busca articular tecnologia, inteligência de dados, apoio a projetos públicos e boas práticas de governança, em diálogo com atores públicos e sociais. Seu arranjo institucional está em estruturação; informações oficiais serão divulgadas pelos responsáveis quando definidas.`

**Soluções**

- Lede: `Áreas em que o InovaTHE busca atuar:`
- Cards (title — subtitle):
  - `Aplicativos e sistemas` — `Frente prevista para apoiar soluções digitais de interesse público, conforme demanda e governança definidas.`
  - `Observatórios e inteligência de dados` — `Uso de dados para apoiar decisões públicas baseadas em evidências, quando houver arranjo definido.`
  - `Apoio metodológico a planejamento e projetos públicos` — `Apoio a planejamento estratégico e governança de projetos, sem assumir mandato de operação.`
  - `Apoio a projetos e eventos` — `Articulação e suporte a iniciativas e eventos de inovação de interesse público.`
  - `Inovação aberta e ecossistema` — `Aproximação entre poder público, academia e sociedade em torno de desafios da cidade.`
  - (EXCLUDED from this build — the "Capacitação, laboratórios e comunidade" line needs operator confirmation of the mandate, which a non-interactive agent cannot obtain, so it is omitted. Do not render it.)

**Transparência (provisional block)**

- Heading: `Transparência`
- Body: `Caso o arranjo institucional venha a prever obrigações de transparência, esta área poderá reunir, se aplicável, documentos públicos relacionados à atuação do InovaTHE. Nenhum documento, contrato, edital ou prestação de contas é publicado neste site provisório.`

**Editais e oportunidades** — NOT a separate section in this build. It is folded into the Transparência block above (whose body already references editais). The standalone copy below is retained only for a future revision, not rendered now: `Esta área será utilizada para divulgar editais e oportunidades quando o arranjo institucional e o responsável estiverem definidos.`

**Contato (provisional, NON-collecting — no form, no contact data)**

- Heading: `Contato`
- Body: `O canal oficial de contato será informado quando o arranjo institucional e o responsável pelo atendimento estiverem definidos. Este site provisório não coleta dados pessoais e não disponibiliza formulário, e-mail, telefone ou perfis sociais oficiais.`

**Footer**

- `InovaTHE — iniciativa de inovação cívica para Teresina`
- `Site institucional provisório, em estruturação.`
- `© {ano} InovaTHE` (compute the year at build time; do not hardcode a year that will go stale)
- No partner logos, no social icons, no address, no phone, no e-mail.

**Utility & accessibility strings** (required by the a11y baseline; render verbatim — these are the only strings allowed outside the section copy above)

- Skip link: `Pular para o conteúdo`
- Logo / home link: `aria-label="InovaTHE — página inicial"`
- Primary nav landmark: `aria-label="Navegação principal"`
- Footer landmark: `aria-label="Rodapé"`
- Mobile menu toggle: `aria-label="Abrir menu"` (closed) / `aria-label="Fechar menu"` (open)

## Service / Activity Lines (internal reference)

These are the conceptual activity lines; their public-facing titles + subtitles are fixed in the Copy deck above. Frame all as **areas of intended activity** (governance rule 2), never delivered services:

- Aplicativos e sistemas
- Observatórios e inteligência de dados
- Apoio metodológico a planejamento e projetos públicos (softened from "Governança, planejamento e PMO público" — "PMO público" reads like an operating mandate the institute does not hold)
- Apoio a projetos e eventos
- Inovação aberta e ecossistema

**EXCLUDED from this build:** "Capacitação, laboratórios e comunidade." It would require operator confirmation of the mandate, which a non-interactive agent cannot obtain, so it is omitted by default. Add it only in a future operator-reviewed revision.

> Note: a governance/PMO-methodology line is well-supported by the InPACTA reference (one of its three core service lines) and anchors the governance half of the blended positioning — kept, but worded as methodological *support*, not an operating mandate.

## Visual Identity Direction

Design InovaTHE as a credible civic-innovation institution, not a startup, SaaS product, or digital agency.

Visual system:

- Deep green/teal institutional base
- Off-white content surfaces
- Dark ink text
- Warm yellow/sun accent, used sparingly for action or wayfinding
- Sober, readable sans typography with strong Portuguese readability
- Simple wordmark treatment for `InovaTHE`

Logo/mark direction (first pass = restrained):

- **First pass: a plain `InovaTHE` text wordmark in the chosen typeface — no symbol, no pictorial cue** (a cue invites a judgment call and risks drifting into representational imagery). Favicon: a simple typographic monogram or a solid-color SVG, one color only. Any mark is a non-binding proposal (governance rule 4).
- Do not invest in an elaborate multi-motif identity exploration — the institute has no confirmed name authority yet, and that work would be discarded.
- The mark must work in one color at header, footer, and favicon sizes.
- Avoid skyline clichés, circuit-board clichés, generic startup marks, decorative clip art.

Accessibility, SEO & privacy baseline (**REQUIRED** in the first pass — cheap, high-credibility, and expected of a Brazilian public-facing civic site):

- Semantic landmarks (`header`/`nav`/`main`/`footer`), correct heading order, a skip-to-content link
- Visible keyboard focus, labeled controls, no keyboard traps, AA color contrast, `prefers-reduced-motion` respected
- The structural checklist above (landmarks, heading order, labeled controls, focus, contrast, reduced-motion) is the **required gate**; an automated axe/Lighthouse pass in QA is encouraged but optional, so a missing tool never stalls the run
- Portuguese `<title>` + meta description (from the Copy deck); no English boilerplate anywhere
- `robots: noindex` until the operator approves public launch — a provisional site must not be indexed as if it were an official entity
- **No `Organization`/JSON-LD structured data** until legal status is confirmed (structured data asserts entity facts to search engines)
- No analytics or third-party tracking scripts (governance rule 7)
- **Do NOT build a separate `/acessibilidade` page** in this first pass (it has no Copy-deck entry and would require invented copy). Defer to a future revision.

Design rejections:

- Purple SaaS gradients
- Generic dashboard imagery
- Decorative blobs/orbs
- Excessive card stacking
- Monotone green-only palette
- Hidden mobile trust paths
- Placeholder sections that look unfinished or abandoned

## UX Requirements

Hero:

- Clearly name InovaTHE.
- Communicate the Teresina-facing civic-innovation role using the governance-safe H1 + provisional subhead (governance rule 1).
- Use the two Copy-deck CTAs (Primary → `#o-instituto`; Secondary "Ver informações de contato" → `#contato`) plus the visible trust-shortcuts row. The secondary CTA is an honest anchor to the provisional contact block — it must not imply a live contact channel.

Mobile (360–390px):

- Brand remains visible.
- Contact/action path remains visible.
- The first-level menu exposes the four primary-nav items (`Início`, `O Instituto`, `Soluções`, `Contato`); the provisional Transparência block is reached only via the in-page trust-shortcuts row, not the menu (avoids amplifying an "abandoned" feel).
- No Portuguese label overlap at 360–390px widths.

Empty / provisional states:

- Must look intentional and in-progress, governed by content rule 3 — operational, not abandoned, and never presupposing entity legitimacy.

## Implementation Plan

### Task 1: Reference Audit (DONE — captured in this plan)

The INPACTA and INOVATEC-JP analyses are already folded into "Reference IA Findings" above. No live re-fetch is required. Any InovaTHE-specific institutional fact is treated as UNKNOWN and handled by the provisional copy per "Decided Assumptions" below — the agent does not research, infer, or resolve these.

### Task 2: Scaffold the standalone project

**Owner:** Programmer-Frontend

**Steps:**

1. **Target-directory rule (non-interactive):** if `~/Workspace/Agent/inovathe-site` does not exist, create it; if it exists and is empty, use it; if it exists and is NON-empty, STOP and report an environment blocker — do not delete, overwrite, or rename anything.
2. **Scaffold non-interactively** — pass every option so `create-next-app` never prompts:
   `npx --yes create-next-app@latest inovathe-site --ts --tailwind --eslint --app --src-dir --use-npm --import-alias "@/*"`
   (`@latest` pins the Next 16.x line as of 2026-06-17; if the installed version shows a Turbopack or any other prompt, pass the matching non-interactive flag rather than answering interactively). Then add test deps: `npm i -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @vitejs/plugin-react`.
3. Set `output: "export"` (+ `images: { unoptimized: true }`) in `next.config.ts`; add `vitest.config.ts` (jsdom env, `@`→`src` alias) and a `test` script (`vitest run`).
4. `src/app/layout.tsx` with `lang="pt-BR"`, InovaTHE metadata (from the Copy deck), `robots: noindex`, and a favicon (non-binding proposal — governance rule 4).
5. Project-scoped Tailwind/stylesheet. No shared tokens.
6. **Commit lifecycle:** after Task 5 validation passes, make exactly ONE commit on the new repo — `feat: scaffold InovaTHE institutional site (first pass)` — staging only named files (never `git add -A`). Do not add a remote, push, or deploy. Leave the committed repo for operator review.

**Acceptance criteria:**

- A clean repo separate from `openclaw-mission-control`.
- `npm run build` (with `output: "export"`) produces `out/index.html`; `npm run test` runs green.
- `<html lang="pt-BR">`, an InovaTHE `<title>`, and `robots: noindex` are set.

### Task 3: Tests first (TDD)

**Owner:** Programmer-Frontend

Write these **failing** tests before implementing the homepage:

1. **Homepage content/semantics test** — asserts only Copy-deck strings: the nav labels (`Início`, `O Instituto`, `Soluções`, `Contato`), the safe H1, the provisional subhead, and the `Áreas em que o InovaTHE busca atuar:` lede. **Do NOT assert any string copied from a reference site.**
2. **Negative governance tripwire** — a `governanceSafety()` helper, first unit-tested against known-bad example strings (so it provably catches them), then run over the page's rendered **text AND** its metadata, `alt`/`aria-label` attributes, `href`/`src` values, and any public asset filenames. It flags: CNPJ patterns (punctuated `\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}`, bare `\d{14}`, and spaced variants), BR phone and CEP patterns, e-mail addresses and `tel:`/`mailto:`/`wa.me` links, and authority/claim strings (`órgão oficial`, `autoridade responsável`, `parceiros oficiais`, `garante atendimento`, `coordena a política`). **This is a best-effort tripwire, NOT proof of governance compliance** — exact-token matching misses synonyms, reformatted identifiers, and imagery, so the human/Architect review in Task 6 remains the real gate.
3. **Metadata/lang test** — asserts `lang="pt-BR"`, the InovaTHE `<title>`, and `robots: noindex`.

**Explicitly prohibited copy (must not appear in any test or on the page):**

- `"ponte entre academia, governo"` — confirmed lifted from INOVATEC-JP's own positioning copy; banned by the no-copy rule.
- `"seja pesquisador"` / any research-recruitment claim — implies an unconfirmed research/labs mandate (that activity line is conditional).

There is no `AuthProvider` in a standalone project, so there is **no auth-boundary test** (v1 required one; removed).

**Acceptance criteria:**

- Test failure is observed before implementation.
- `governanceSafety()` passes against the rendered page AND is itself proven to catch the known-bad examples (a helper that flags nothing is worthless).

### Task 4: Homepage implementation

**Owner:** Programmer-Frontend

Build the tight first-pass IA (`Início`, `O Instituto`, `Soluções`, `Contato`, plus a single provisional `Transparência` block that also covers editais) to make Task 3's tests pass, following the content-governance and visual-identity rules.

**Acceptance criteria:**

- Homepage matches the approved IA and the governance-safe copy.
- Service lines are framed as intended areas of activity (rule 2).
- No fabricated facts, logos, partner marks, or photos (rules 4–6).
- UI works at desktop (1440px) and mobile (390px).

### Task 5: Validation

**Owner:** Programmer-Frontend, then QA

**Commands (all REQUIRED — do not skip the build):**

- Targeted homepage + governance tests
- Lint
- Test
- `build` — **required**, not optional. With `output: "export"`, confirm the build produces `out/index.html`. It is the cheapest catch for regressions; do not substitute it away.

If a command is genuinely blocked, record the exact blocker and run the strongest targeted substitute, but the build must be attempted.

**Browser checks:**

- Desktop screenshot ~1440px and mobile screenshot ~390px.
- No text overlap at 360–390px.
- No hidden first-level trust paths; provisional sections are not featured as if live.
- No reference-site copy and no fabricated institutional facts on the page.

**Subjective criteria** ("feels like a credible civic institution, not SaaS/agency") are **deferred to human/Architect review in Task 6** — an autonomous agent cannot self-certify them.

### Task 6: Review handoff

**Owner:** Supervisor

**Steps:**

1. Collect implementation summary, changed files, tests, and screenshots.
2. Explicitly label any generated logo/wordmark/favicon as a non-binding proposal.
3. Route to the Architect/QA board agents (not the human operator) for review of the subjective design-credibility criteria — this is async delegation within the board, not a mid-run question.
4. Return to operator for approval before any deployment.

## Execution Guardrails (footgun prevention)

- **Stay inside the InovaTHE repository.** Do not read, edit, delete, stage, or commit any file outside the new InovaTHE repo. (This one rule replaces v2's Mission-Control-specific cleanup steps — referencing the MC repo at all was itself a footgun in a standalone spec.)
- **Staging discipline:** never `git add -A` / `git add .`; stage only explicitly named files.
- **No data collection and no third-party scripts** (content governance rule 7).
- **No production deploy, hosting setup, domain registration, `robots` un-blocking, gateway restart, or live-host mutation** at any task — these are operator decisions. Applies to Tasks 4 and 5 (programmer/QA), not just the Supervisor handoff.

## Decided Assumptions (the agent does not ask — all resolved here)

The Supervisor agent is non-interactive, so these are **decisions, not questions.** Several InovaTHE institutional facts are genuinely unknown; for every one, the decided default is the governance-safe provisional path already specified above. The agent must NOT research, infer, ask about, or attempt to resolve any of these — it builds the provisional version.

| Unknown fact | Decided default for this build |
| --- | --- |
| Legal nature (institute / program / foundation / etc.) | Treat as undefined — "arranjo institucional em estruturação." No legal status stated. |
| Owning authority / official responder | Not named. Copy: "informações oficiais serão divulgadas pelos responsáveis quando definidas." |
| LAI / ouvidoria / editais / contracts / prestação de contas | Not operated. One provisional Transparência block; no portal, no documents, no LAI request channel. |
| Projects, partners, logos, addresses, phones, emails, documents | None shown. Projetos / Notícias / Parcerias deferred (not built). No contact data (non-collecting). |
| Capacity building / labs / community mandate | EXCLUDED — the "Capacitação, laboratórios e comunidade" line is omitted. |
| Repo / package manager / Node / stack | `~/Workspace/Agent/inovathe-site`, npm, Node 20.9+ (22 LTS), Next 16 + TS + Tailwind + Vitest, static export. |
| INPACTA reference identity (Maringá consultancy vs Mossoró tech park) | Use the captured structural patterns regardless; the identity flag does not change this build. |
| Indexing | `robots: noindex` — stays noindex; the agent never un-blocks indexing. |
| Deploy target | Production host `root@192.168.2.62:~/inovathe` (static `out/` served from there). Build only; deploy is a post-approval step, not the implementing agent's job. |

These remain real open facts for a **future operator-reviewed revision** (once InovaTHE's legal arrangement, authority, projects, and channels are confirmed). They are recorded here for that future pass — not as work for, or questions during, this build.

**Rule:** every section that would assert an entity function (Transparência, editais, contacts, partners) stays provisional in this build, because those facts are unknown and the agent does not resolve them.

## Supervisor Handoff Text

Use this when unpausing the MC task after operator approval:

```text
Approved plan: build a first-pass InovaTHE institutional landing page as an INDEPENDENT standalone project. Execute end-to-end, NON-INTERACTIVELY — every decision is in the plan; do not stop to ask anyone. Decided stack: repo ~/Workspace/Agent/inovathe-site, npm, Node 20.9+ (Node 22 LTS), Next 16 + TypeScript + Tailwind + Vitest, static export (output:"export"). The agent does NOT deploy — produce the static out/ build and stop at the Task 6 review handoff. (Known deploy target for the post-approval step: root@192.168.2.62:~/inovathe.) Do NOT read, edit, or touch the OpenClaw Mission Control repo or any file outside the new InovaTHE repo.

Positioning: civic-innovation + governance blend (INOVATEC-JP project model + InPACTA transparency/governance structure). Treat both reference sites as IA/positioning references only — do not copy their text, design, logos, or claims. Specifically banned copy: "ponte entre academia, governo" (copied from INOVATEC-JP) and "seja pesquisador".

First-pass IA (tight): top nav = Início, O Instituto, Soluções, Contato. A single provisional Transparência homepage block (covers editais too — no separate Editais section), not in top nav. EXCLUDE the "Capacitação, laboratórios e comunidade" line. Defer Projetos, Notícias, Parcerias.

Use the plan's Copy Source-of-Truth deck VERBATIM for every visible string — render only deck strings; if an element lacks deck copy, omit it rather than invent. Do not invent CNPJ, legal status, addresses, team names, contracts, edital data, partner names, phones, counts, or outcomes. Any generated logo/wordmark/favicon is a non-binding proposal; no partner/sponsor logos; NO photographic or representational imagery (abstract/typographic only).

Hard rules: NO data collection or third-party scripts — no contact form, mailto/tel, social links, maps, analytics (LGPD: no confirmed data controller). Static export must produce out/index.html. robots: noindex until launch approval. No Organization/JSON-LD until legal status confirmed. Required a11y/SEO baseline (semantic landmarks, skip link, visible focus, AA contrast, reduced-motion, no critical axe violations, PT-BR title/description).

Content governance is mandatory: hero H1 may state purpose but not authority/outcome; the subhead must use a provisional verb (busca / atua para apoiar / em diálogo com); service lines are framed as "áreas em que o InovaTHE busca atuar," not a delivered catalog; empty states must not presuppose entity legitimacy.

TDD required: homepage content test (Copy-deck strings only), a governanceSafety() tripwire (unit-tested against known-bad examples, then run over rendered text + metadata + alt/aria + href/src + asset names; it is a tripwire, not proof — human review is the gate), and a metadata/lang test (lang="pt-BR", noindex). No auth-boundary test (standalone, no auth). Build is required in validation and must emit out/index.html. Browser QA verifies desktop/mobile screenshots, no overlap, no reference copy, no fabricated facts.

Guardrails: stay inside the InovaTHE repo; never git add -A; stage only named files; no production deploy, hosting setup, domain registration, robots un-blocking, gateway restart, or live-host mutation without explicit operator approval.
```
