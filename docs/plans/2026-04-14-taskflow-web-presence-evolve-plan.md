# TaskFlow Web Presence — Improve / Evolve Plan

**Status:** Draft v1.3 (amended after Codex adversarial review), not yet implemented
**Date:** 2026-04-14
**Source audit:** 10-agent source/DOM review + 4-agent pure-visual browser review + 3-agent human-perspective review + Codex (gpt-5.4/high) two-round adversarial review (all 2026-04-14)
**Target codebase:** the Dev Squad's **TaskFlow marketing site** — a Vite + React 19 + Tailwind 4 + i18next project at `/root/.openclaw/workspace/workspace-mc-3461451b-5824-4ed0-872c-d14d5d2be107/taskflow-web-presence-vite/` on the gateway machine `192.168.2.60`, copied locally to `/tmp/tf-site/` for audit purposes. This is **NOT** the `frontend/` directory in this repo (which is the separate Next.js Mission Control dashboard for a different product). The plan file lives in `openclaw-mission-control/docs/plans/` only as a shared reference for the Supervisor and Dev Squad.
**Target milestone:** take the site from "v0.5 visual prototype" to "v1.0 shippable commercial/government marketing site"
**Scope excluded:** backend/product code changes, real testimonial collection, real logo licensing, third-party integrations. This plan is about making the marketing site honest, complete, and rep-worthy. Not about building new product features.

**Amendments in v1.1** (2026-04-14, after a 4-agent Chrome-DevTools-MCP visual audit and operator design feedback on the hero composition and navigation):
- Three new findings the source audit missed: (1) frosted glass on docs is cosmetically broken — classes present, visual effect absent; (2) docs code blocks are visually indistinguishable from body prose; (3) docs mobile/tablet users are functionally locked out of sidebar navigation (no hamburger trigger).
- Severity upgrade on the docs mobile hamburger item from MEDIUM to HIGH.
- New Phase 3 Track E for visual/aesthetic correctness.
- New section 10 (Methodology note) on why a visual audit layer is required in every future QA pass, not just DOM assertions.
- **Hero composition rebuild** (Phase 1 Track A): the Landing hero's right column replaces three floating specks on bare navy with a layered composition — a Kanban dashboard screenshot as the base plate inside a browser frame, with the WhatsApp bubble and Arc Connector overlaid on top so the arc points at an actual task card. This subsumes the separate Product Preview section conditionally.
- **Nav Sign In + Get Started CTA** (Phase 1 Track C): adds the missing login and primary CTA to the top-right of the nav. Plan-level gap — the original design plan did not specify either, which is why the audit agents did not flag it. The omission is corrected here.

**Amendments in v1.2** (2026-04-14, after a 3-agent human-perspective review that replaced defect-hunting with skeptical-buyer memo and senior-designer craft critique):
- Four new Phase 3 Track E items from the designer craft review: (1) feature card variation — kill the Tailwind-uniform look; (2) hierarchy cascade rule — H1 fix must restore whole-page hierarchy, not just contrast; (3) docs color rhythm — teal callout boxes + teal-accented code blocks to break up white-on-gray prose; (4) docs type rhythm — tighten body line-height from ~1.7+ to ~1.55–1.6.
- Section 10 (Methodology) gains a role-design note on why the naive-first-visitor LLM role failed (superhuman vision reads dark-on-dark text; polite-reviewer default overrides "be blunt" instructions) and which roles work instead (adversarial-buyer memo, senior-designer critic with named comparables).
- Sharpened framing from agent 2 (adversarial buyer) and agent 3 (designer critic) incorporated into the current-state paragraph and Phase 3 Track E track description.

**Amendments in v1.3** (2026-04-14, after a two-round Codex adversarial review with gpt-5.4/high/fast that retracted one wrong critique but sharpened seven others against the actual TaskFlow source):
- **Header now names the target codebase explicitly** (the first round of Codex review failed because the plan never stated which project it was talking about — Codex went looking in this repo and audited the wrong thing).
- **Phase 1 Track C invisible-heading fix revised.** The root-cause diagnosis (global `h1-h6 { color: var(--navy) }` rule overrides inherited `text-white`) is correct. The originally proposed fix (drop the global color rule) is collateral-heavy because the rule is load-bearing on every light-background heading across all 3 pages. Safer fix: add `text-white` (or appropriate token) directly on every dark-surface heading element. Better structural fix as a follow-up: refactor heading color out of global selectors entirely and scope it via `.prose` wrappers or component-level classes.
- **New Phase 1 Track C item: Navigation.jsx has hardcoded English strings.** Codex caught that the nav imports `useTranslation` for language switching but renders `Home`, `Product`, `Docs` as hardcoded strings. These must be wrapped in `t()` calls in Phase 1, not deferred to Phase 2. The i18n problem is worse than the plan originally stated: even the chrome that "works" when you switch locale is partially fake.
- **Phase 1 duration bumped from 2-3 days to 4-5 days.** With the hero composition rebuild, 3 missing sections, Product hero rewrite, Government use case add, CTA button, invisible-heading fix across all affected headings, nav sign-in/Get-Started additions, nav hardcoded-string wrapping, nav overlap fix, and a11y form field fixes, 2-3 days was fake. 4-5 days of PF full-time is realistic.
- **Phase 2 Track A i18n duration bumped and acceptance gate strengthened.** Extracting ~500 keys + translating into PT/ES/FR + metadata localization + QA test repair is ~4-5 days, not 2. The "matching key counts" gate is replaced with: a Playwright test that asserts **zero known English anchor phrases render in the non-EN locales**, plus a lint that fails if any `t()` call returns its own key (missing translation fallback).
- **New Phase 2 Track E: Commercial foundation (SEO + legal + performance + analytics).** The original plan omitted OG tags, Twitter cards, sitemap.xml, robots.txt, canonical URLs, privacy policy, terms of service, cookie consent, image optimization, preconnect/preload hints, performance budgets, and analytics scaffolding — all basic requirements for a v1.0 commercial marketing site. A Vite SPA has no SSR framework metadata layer, which makes this problem more acute than it would be for a Next.js or Astro site. Track E covers the structural/scaffolding work; pricing page and 404 page scaffold go in Phase 1 Track D (new, cheap wins); real content for legal pages blocks on operator input in Phase 4.
- **Phase 3 Track E split into Track E (correctness) and Track F (design pass).** Codex flagged that Phase 3 was labeled "No new content, just correctness" but Track E mixed frosted glass / code block treatment / type rhythm (correctness) with feature card variation / hierarchy cascade / docs color rhythm (design judgment). Split them: Track E stays correctness, Track F is an explicit design pass that the plan no longer pretends is polish.
- **New Phase 3 Track E item: Global color architecture refactor.** Codex surfaced that the pattern `body { color: var(--gray-600) }` + `h1-h6 { color: var(--navy) }` is fragile in a utility-first codebase — every dark-surface section has to fight the global default. Fix during Phase 3: remove color declarations from global element selectors entirely, add them back via a scoped `.prose` wrapper or component-level classes. This is the "better structural fix" promised in the v1.3 header note.
- **Hero rebuild must be mobile-first, not desktop-first.** Codex warned that the dashboard-screenshot-behind-bubble composition could read fine at 1440px and fail at 375px — the target card becomes microscopic, the overlay competes with the headline, the arc becomes decorative noise. Phase 1 Track A is revised: Architect designs the mobile composition first at 375px, then scales up to 768 and desktop. Desktop is an enlargement of the mobile-correct layout, not the reverse.
- **Methodology section 10.1 softened.** The "skip the naive-first-visitor role" rule is downgraded from "never use" to "use with caution, requires adversarial priors to break the polite default." The "always require adversarial-buyer and designer-critic roles" is downgraded from "non-negotiable" to "required for large audits, overkill for small iterative fixes." The "contrast judgment rule" is replaced with a measurable WCAG contrast ratio check (4.5:1 body / 3:1 large text) — no more eyeball estimation by the agent.
- **Framer Motion + prefers-reduced-motion reinforced.** Codex confirmed the plan's existing Phase 3 Track A fix is correct: the CSS `@media (prefers-reduced-motion: reduce)` block in `index.css` handles CSS animations but Framer Motion components still animate unless wrapped in `MotionConfig` or a `useReducedMotion()` hook. Explicit call-out kept.

---

## 1. Current state — in one paragraph

The design system (tokens, fonts, components inventory) matches the plan almost perfectly **at the source level** — but the visual audit revealed that one of those wins (the frosted-glass docs aesthetic) is only classwise correct and doesn't actually render as a blur/translucency effect. Everything downstream of the design system is the problem: Landing page is missing 3 conversion-critical sections (Logo Bar, Social Proof, Product Preview), Product page hero has the wrong headline and the 6 "deep-dive" sections are Landing-quality blurbs, the Security card set would fail a Brazilian gov RFP, Docs sidebar is 4/10 complete, there is no Government use case, the i18n is cosmetic (≈8% real coverage — ProductPage and DocsPage have **zero** `t()` calls), motion ignores `prefers-reduced-motion`, sticky nav overlaps content on all 3 pages at mount, and a global `h1-h6 { color: var(--navy) }` rule in `index.css` makes every heading on every dark-background section **invisible** (navy-on-navy) — confirmed by visual audit across all 4 locales. The visual audit also surfaced three additional defects not visible from source: docs code blocks render visually indistinguishable from body prose (no distinct background, no syntax highlighting), the docs frosted-glass look is cosmetically flat (classes present but nothing to blur against), and mobile/tablet users are functionally locked out of docs navigation because the sidebar is hidden with no hamburger trigger. The chassis is right. The content, the i18n wiring, several polish details, and several visual-effect details that were reported as "done" from source are not.

## 2. Target state — the v1.0 definition of done

A prospective buyer — including a Brazilian government procurement evaluator — can:
1. **Land on any of the 3 pages in any of the 4 locales and read 100% of content in that language** (not just nav + hero).
2. **Read the Product page and understand, with concrete proof (screenshots, specific feature copy, real integrations), what TaskFlow does** — and find a Government & Public Sector use case explicitly called out.
3. **Read the Security & Data section and satisfy a basic RFP compliance checklist** (encryption at rest, encryption in transit, data residency, tenant isolation, audit logs, RBAC, retention, LGPD/GDPR, self-host) without having to "Contact us" for the answer.
4. **Use the Docs page as an actual documentation portal** — search works, API reference is complete enough to integrate against, and every sidebar item leads to real content, not a stub.
5. **Browse from any viewport (375 / 768 / 1440)** without horizontal overflow, overlap, or sub-44px tap targets, with `prefers-reduced-motion` honored.
6. **Receive no visual or motion artifacts that contradict the design system** — navy-dominant, teal-action, purple-accent hierarchy holds everywhere, and every heading is legible on every background.

## 3. Phased roadmap (4 phases, ≈2 weeks)

The audit produced ~30 distinct findings plus one critical rendering bug discovered during plan review. Dropping them as one giant punch-list would overwhelm the Dev Squad and tangle the review cycle. Phase them so each phase produces a coherent, reviewable release with its own QA pass.

| Phase | Theme | Duration | Release tag | Gate |
|---|---|---|---|---|
| **Phase 1** — Unblock | Fix the embarrassments. Right words in right places. Critical structural gaps. Invisible-heading P0. Mobile-first hero rebuild. Sign In / Get Started. Hardcoded nav strings. Commercial basics scaffold. | **4–5 days** *(bumped from 2-3 in v1.3)* | v0.6 | Architect visual QA at 375 → 768 → 1440 × EN |
| **Phase 2** — Substance + Commercial foundation | i18n coverage expansion. Security rewrite. Feature deep-dives. Docs content. SEO / legal / performance / analytics scaffolding. | **8–10 days** *(bumped from 5-7 in v1.3)* | v0.7 | Architect copy QA + QA-Unit i18n anchor-phrase test + Lighthouse SEO ≥ 90 |
| **Phase 3** — Polish (correctness) + Design pass | Track E: a11y, responsive breakpoints, motion reduced-motion, TOC auto-gen, code highlighting, global color architecture refactor, frosted glass, type rhythm. Track F: feature card variation, hierarchy cascade verification, docs color rhythm. | 4–5 days | v0.8 | QA-E2E full responsive pass + Architect design-pass sign-off |
| **Phase 4** — Content production | Real video, real testimonials, real customer logos, SOC 2 language, pricing page content, legal page content. Gated on external (operator input). | ongoing | v1.0 | Operator sign-off |

Work within a phase can run in parallel across tracks; phases themselves are sequential because each raises the bar for the next. i18n refactoring in Phase 2 must happen before the Phase 3 QA-E2E multi-locale run, or QA-E2E will be chasing shadows.

---

## Phase 1 — Unblock (v0.6, 2–3 days)

**Theme:** Stop shipping obviously wrong things. One-line-fix energy.

### Track A — Landing page structural gaps
- **[NEW in v1.1, revised mobile-first in v1.3] Rebuild the hero visual composition with a dashboard screenshot as the base layer.** Currently (LandingPage.jsx:72–112) the right column of the hero holds three absolute-positioned floating elements — a WhatsApp chat bubble, the Arc Connector, and a small "Q3 Budget Report" browser mockup — on a bare navy background. Visually the composition is unbalanced: a dense headline on the left, three disconnected floating specks on the right, and a large navy void between them. The fix is to replace the bare-navy right column with a layered composition:
  1. **Base layer:** full Kanban dashboard screenshot inside a `BrowserMockup` frame. Use the existing `public/screenshots/kanban-full.png` (already in the repo — no new asset needed). `dashboard-home.png` is an acceptable alternative depending on which view reads better at the hero scale.
  2. **Overlay 1:** the existing WhatsApp chat bubble (`bg-[#DCF8C6]` card from LandingPage.jsx:81–84), positioned at the upper-left of the composition with a slight negative offset so it visually "floats in front of" the browser frame.
  3. **Overlay 2:** the Arc Connector SVG, redrawn to originate from the WhatsApp bubble and **terminate on a specific Kanban card inside the dashboard screenshot** — the arc should have a real destination, not a trailing-off curve. This tells the story "chat message → task card" immediately.
  4. **Shadow + lift:** the browser frame gets `shadow-2xl` so it reads as "the product" not "a background image." The chat bubble and arc are the "incoming action" layer.

  **[v1.3 critical addition] Mobile-first design process, not desktop-first.** Codex warned that the above composition could read fine at 1440px and collapse at 375px — the dashboard screenshot inside a browser frame becomes microscopic, the chat bubble overlay competes with the headline for attention, and the arc connector becomes decorative noise with no clear destination. **Architect must design the 375px composition first**, then 768, then 1440. Desktop is an enlargement of a mobile-correct layout, not the reverse. At 375px the composition may need to be: stacked vertically (headline above, then a smaller BrowserMockup, then CTA buttons), with the chat bubble and arc simplified to a single static illustration or hidden entirely. Phase 1 Track A ships to QA only after the 375px composition has been approved, not before. This is non-negotiable.

  This change also **subsumes the separate Product Preview section below** (originally plan 238–241) — the dashboard is now visible in the hero, which is where conversion-optimized SaaS sites want it anyway. Keep the later Product Preview section only if the dashboard in the hero is too small to read at typical viewport sizes; otherwise delete it from the scope and reclaim the vertical space.
- **Add Logo Bar section** after Hero, before Features (plan 195–198). If no real logos exist, implement with `visibility: hidden` and a single `TODO: enable when 3 logos collected` comment. Component: `LogoStrip`, add to `src/components/`. Keep plan's "hide until 3 logos" directive.
- **Product Preview section** (plan 238–241) — **conditionally cut** depending on hero rebuild. If the hero dashboard composition is legible at 1440px and mobile, this section becomes redundant and should be removed. If the hero dashboard is too small to be useful, keep this section with its own full-width dashboard + annotation callouts as originally planned. Architect decides after seeing the hero rebuild at Phase 1 review.
- **Scaffold Social Proof section** at the correct position (plan 220–227). Honor the plan's "ship HIDDEN until 2+ real testimonials" rule: render the section only if `testimonials.length >= 2`, and leave the array empty. Component: `TestimonialCard` in `src/components/`. Dormant but buildable.

### Track B — Product page critical fixes
- **Rewrite ProductPage.jsx:101 hero** — replace "Teams that ship with TaskFlow" with "See what TaskFlow **actually does**" (plan line 260). Update purple-underline positioning accordingly.
- **Add the Government & Public Sector use case** as the 5th card in the Use Cases section (plan 300). Copy draft: *"Inter-departmental task delegation with hierarchical boards and full audit trails. Every action logged, every decision traceable. LGPD-compliant, self-hostable, zero third-party data sharing."* Bump Use Cases grid from `md:grid-cols-2` (4 cards) to `md:grid-cols-2 lg:grid-cols-3` (5 cards).
- **Add the missing CTA button** to the Product page Bottom CTA section (ProductPage.jsx:391–402). Primary "Get Started Free", secondary "Talk to Sales".

### Track C — Global visual and layout fixes
- **P0: Invisible headings on dark backgrounds.** `src/index.css:60-65` declares `h1, h2, h3, h4, h5, h6 { color: var(--navy) }` as a global rule. This has higher specificity than a `text-white` utility applied on a parent `<div>`, because CSS inheritance is weaker than a direct element rule. Result: every heading on every navy-background section (Landing hero, Landing How-it-Works, Landing Stats, Landing Bottom CTA, Product hero, Product Use Cases, Product Bottom CTA, Footer headings) renders navy-on-navy and is **invisible**. **[v1.3 revised fix — Codex adversarial review]** The originally proposed fix (drop the `color: var(--navy)` from the global selector) is collateral-heavy: the global rule is load-bearing on every light-background heading across all 3 pages, and removing it would silently regress every prose-on-white heading in Features, Stats (on gray-50), Docs content, Footer columns on light bg, and many more. The safer Phase 1 fix: **add `text-white` (or the appropriate token) directly on every dark-surface heading element** — audit every `<h1>/<h2>/<h3>` in the 3 pages, grep for the ones whose parent has `bg-[var(--navy)]` or similar dark background, and add an explicit `text-white` class on the heading itself. This is a larger patch (probably 10-15 touches across the 3 pages) but carries zero collateral risk. The structural refactor (move heading color out of global selectors entirely, scope via `.prose` wrappers) is deferred to Phase 3 Track E as a **global color architecture refactor** — that's the right place to do a breadth-sensitive change once the site is stable.
- **[NEW in v1.3] Wrap hardcoded nav strings in `t()`.** Codex caught that Navigation.jsx imports `useTranslation` for language switching but renders `Home`, `Product`, `Docs` as hardcoded English strings. These must be wrapped in `t('nav.home')`, `t('nav.product')`, `t('nav.docs')` — the keys already exist in `src/locales/en.json` (lines 3-5) and in the other 3 locale files. This means the current delivery's i18n is even more cosmetic than the initial audit described: even the chrome that appears to translate when you switch locale is partially fake. Fix is trivial (replace 3 string literals) but must land in Phase 1 as part of the invisible-heading + nav additions work.
- **[NEW in v1.1] Add Sign In + Get Started CTA to the top-right of the nav.** The current Navigation.jsx renders `Home | Product | Docs | EN PT ES FR` and nothing else on the right side. Every SaaS marketing site is expected to have a clear path to "I am a returning user, let me log in" and "I am a new user, let me start." The absence of both is jarring and makes the site read as a brochure, not a product. Plan addition:
  1. **Sign In** — text link (not a button) styled as `text-white/80 hover:text-white`, positioned between the language switcher and the primary CTA. Target: `/login` (route can stub to a placeholder page initially — the existence of the link matters more than a real auth flow for this phase).
  2. **Get Started** — primary button using the existing `Button variant="cta"` component, sits as the rightmost element in the nav. Target: `/signup` or scrolls to the hero CTA.
  3. **Language switcher repositioning** — moves to the left of Sign In, or collapses into a dropdown if nav width becomes crowded. Architect picks the approach.
  4. **i18n:** both new labels (`nav.sign_in`, `nav.get_started`) added to all 4 locale files as part of this track, not deferred to Phase 2.
  This is a plan-level gap, not a delivery gap — the original design plan (2026-04-11-taskflow-web-presence-design.md:171–176) did not specify a Sign In link or a persistent Get Started CTA in the nav. The evolve plan corrects the omission.
- **Fix sticky nav overlap** across all 3 pages. Add `padding-top: 80px` to `main` or `scroll-padding-top: 80px` to `html`. Verify on all viewports.
- **Fix form field a11y warnings** — add `id="language-select"` + `name="locale"` to the language combobox in `Navigation.jsx` and `id="search-docs"` + `name="search"` to `DocsPage.jsx:49`.

### Track D — Commercial basics scaffold (NEW in v1.3)

Codex flagged that the plan ignored all of the basic commercial/marketing foundations a real v1.0 site needs. These cheap structural items land in Phase 1; the content-heavy versions land in Phase 2 Track E.

- **404 page.** Create `src/pages/NotFoundPage.jsx` with a minimal "page not found" layout that uses the existing design system (same nav, same footer, navy + teal, a "back to home" CTA). Wire it into the React Router catch-all route. One hour of work.
- **Legal page placeholders.** Create `src/pages/PrivacyPage.jsx` and `src/pages/TermsPage.jsx` with `<h1>` and a "Last updated: Coming soon" placeholder. Real content blocks on operator in Phase 4. These must exist as routable URLs so the footer links work and so SEO crawlers don't hit 404s. Footer gets links to both.
- **`public/robots.txt`** — allow all, sitemap reference to `/sitemap.xml`.
- **`public/sitemap.xml`** — list `/`, `/product`, `/docs`, `/privacy`, `/terms` at minimum. Can be static for now; Phase 2 Track E makes it per-locale.
- **Base `<head>` meta scaffold.** Add OG title, OG description, OG image (use the existing `hero.png` asset), Twitter card, canonical URL, theme-color meta, and favicon meta to `index.html`. Hard-coded English for Phase 1; Phase 2 Track E makes them per-locale via the i18n hook.
- **Pricing page placeholder.** Create `src/pages/PricingPage.jsx` with a "Pricing" heading and "Coming soon" copy, wire the route. Add to nav as optional — operator decides whether to expose in nav before pricing is real. If not exposed, at minimum the page must exist as a URL for the sitemap and for internal linking later.

These are all cheap wins individually. Together they transform the site from "demo" to "basic commercial scaffold" without needing any content decisions. Roughly half a day of PF work across all 6 items.

### Phase 1 acceptance gate
- All 3 pages render without nav overlap at 375 / 768 / 1440.
- **Every heading on every page is legible on its background** (manual screenshot review by Architect in EN + PT + ES + FR, verified by a measurable WCAG contrast ratio check ≥ 4.5:1 for body headings and ≥ 3:1 for large display headings — not eyeball estimation).
- Landing has 11 sections (the plan's required list) in the plan's order, with Social Proof dormant.
- **Landing hero mobile composition (375px) is approved by Architect before desktop composition is reviewed.** Hero work does not ship to QA until the mobile-first version is signed off.
- Product hero says "See what TaskFlow actually does".
- Product Use Cases has 5 cards, Government included.
- Nav has Sign In + Get Started CTA, and `Home` / `Product` / `Docs` are wrapped in `t()` calls (not hardcoded).
- 404 page, privacy/terms placeholders, robots.txt, sitemap.xml, and base OG meta tags exist.
- No console a11y warnings on any page at any viewport.
- Architect reviews screenshots and posts PASS.
- Supervisor approves → merge → deploy → v0.6 tag.

---

## Phase 2 — Substance (v0.7, 5–7 days)

**Theme:** Make the content honest and deep. This is where the real work is.

### Track A — Full i18n remediation (≈4–5 days, bumped from 2 in v1.3)

This is the largest single piece of work in the whole plan. The mechanical nature makes it parallelizable per page, but Codex (v1.3) correctly pushed back on the original 2-day estimate as fantasy. Extracting ~500 keys, namespace design, JSX wiring, translation into 3 target locales, metadata localization, and test repair is realistically 4-5 days of focused PF work, not 2.

**Step 1.** Expand `src/locales/en.json` from 18 keys to ~500 keys. Every user-visible string in `LandingPage.jsx`, `ProductPage.jsx`, `DocsPage.jsx`, `Navigation.jsx`, `Footer.jsx` gets a translation key. Use a flat namespace per page: `landing.hero.title`, `product.feature_capture.title`, `docs.getting_started.intro`, etc.

**Step 2.** Replace every hardcoded string in the JSX with `t('key')`. Run `rg -n '>(?!<)[A-Z][a-z]' src/pages/` to find remaining offenders. Target: 0 hardcoded strings outside of `src/components/animations/` (which has no user-visible text).

**Step 3.** Translate en.json → pt.json, es.json, fr.json. All 4 locale files must end with the same key count. The 4 existing translations appear human-written, so continue that quality bar.

**Step 4.** Add per-locale meta tags in `src/i18n.js` — `document.title` and `<meta name="description">` updated on language change. Add `helmet-async` or hand-wire `useEffect` in `App.jsx`.

**Step 5.** Re-enable / extend the existing `validate-i18n.spec.js` Playwright test: for each of 4 locales, navigate to each page, assert every heading is in the target language (sample via known translated strings).

**Acceptance (revised in v1.3 — "matching key counts" gate retired):**
1. Switching from EN → PT/ES/FR on any page changes **100%** of visible content (minus brand terms like "WhatsApp", "Kanban", "TaskFlow").
2. A Playwright test asserts **zero known English anchor phrases** render in non-EN locales — sample phrases from the audit: "Everything you need to ship faster", "How it works", "Trusted by teams who ship", "See it in action", "Ready to transform your team's workflow?", plus every `<h1>` and `<h2>` on every page. If any of these appear when `i18nextLng !== 'en'`, the test fails.
3. A lint / pre-commit check fails if any `t()` call returns its own key literal at runtime (translation fallback miss — indicates the key exists in `en.json` but is missing in one of the other locale files).
4. Locale file key counts match across EN/PT/ES/FR as a sanity check, but this is NOT the primary gate — it catches missing keys but not English strings copied into non-English files as placeholders, which was Codex's original pushback.
5. `validate-i18n.spec.js` passes for all 4 × 3 = 12 locale/page combinations.

### Track B — Security & Data rewrite (≈1 day, needs operator input)

The delivered 6 cards are too shallow for an RFP. Rewrite the section as 9 cards in a 3×3 grid with explicit language for each item the audit flagged missing:

1. **Encryption in transit** — "All traffic TLS 1.3, HSTS enforced, no mixed content."
2. **Encryption at rest** — "SQLite database encrypted with AES-256 via `sqlcipher`; backups encrypted client-side before upload."
3. **Data residency** — keep existing copy.
4. **Tenant isolation** — "Per-board SQLite instances by default; no cross-tenant queries possible at the schema level."
5. **Self-hosted deployment** — keep existing.
6. **Audit trail** — keep existing, expand with retention note.
7. **RBAC** — keep existing.
8. **Data retention** — "Configurable per-board from 30 days to indefinite. Right to deletion honored within 7 days."
9. **LGPD / GDPR** — keep existing.

Bottom of section: **SOC 2 status** — rewrite the "Contact us for SOC 2" deflection. Either a specific status ("SOC 2 Type I attestation in progress, expected Q4 2026"), or a link to a security.txt / trust page. "Contact us" is a procurement red flag.

**Input needed from operator / Supervisor before this track can start:** real compliance posture. PF and Architect cannot invent encryption claims. This track is BLOCKED until the operator provides the actual commitments. If the operator wants a placeholder, use "v1.0 commitments, TBD" — do not fabricate certifications.

### Track C — Product page feature deep-dives (≈1.5 days)

Replace the 6 Landing-quality feature blurbs with actual deep-dive sections. For each of the 6 features (Capture / Dashboard / Automations / Search / Hierarchy / Meetings):

- Dedicated section, `py-32`, alternating bg (white / gray-50).
- **Left:** 2-paragraph explanation (~120 words). What it does, how it feels in practice, what it replaces.
- **Right:** A real product artifact. Existing screenshots in `public/screenshots/` cover 6 dashboard views already — repurpose them with `BrowserMockup` frames. For "Capture", use a WhatsApp chat bubble mockup (component already exists at LandingPage.jsx:81). For "Hierarchy", draw a 3-level org tree SVG.
- **Bullet proof list:** 3 concrete, specific benefit bullets. "Auto-parses dates from natural language ('by next Friday' → 2026-04-24)" beats "Smart automations".

This is the single biggest quality lift on the Product page. Without it the page reads as a second Landing page.

### Track D — Docs content fill-in (≈2 days, parallelizable per section)

Add the missing sidebar sections:
- **Core Concepts** — 4 subsections (Boards & Columns, Tasks & Lifecycle, People & Assignment, WIP Limits). ~300 words each.
- **Troubleshooting** — 4 subsections (Setup Issues, WhatsApp Connection, Board Not Syncing, Common Errors). Real error messages, real remediation steps.
- **Admin & Deployment** — 5 subsections (Self-hosted Setup, Environment Variables, Backup & Restore, Security Configuration, Multi-language Setup). This one needs input from DevOps agent for accuracy.
- **Changelog** — wire a simple auto-generated page backed by `public/changelog.json` or similar.

**API Reference track (separate):** The delivered 5-endpoint stub is not viable. Needs a backend inventory. This track requires the Programmer-Backend agent to produce a canonical endpoint list from the real MC API (`/api/v1/agent/...` routes — there are ~50 endpoints already). Once PB delivers the inventory, PF wires it into DocsPage in a scannable table format per plan line 348.

### Track E — Commercial foundation (NEW in v1.3)

Codex's critique #7 was the most important miss in v1.2: the plan claimed to produce a "v1.0 shippable commercial/government marketing site" without addressing any of the basic commercial foundations. This track closes that gap. Phase 1 Track D already scaffolded the cheap-win structure (404, privacy/terms placeholders, robots.txt, base OG meta); Phase 2 Track E does the content + performance + analytics work.

- **Per-locale metadata.** Wire `<title>`, `<meta name="description">`, `<meta property="og:title">`, `<meta property="og:description">`, `<meta property="og:image">`, `<meta property="og:url">`, `<meta property="og:locale">`, `<meta name="twitter:card">` into the i18n hook so they update on locale change. Use `react-helmet-async` (or hand-wire a `useEffect` on `i18n.language`). Each of the 3 pages needs its own title + description per locale — so 3 pages × 4 locales = 12 title/description pairs, all in the locale JSON files.
- **Canonical URLs per page.** `<link rel="canonical" href="...">` on each page, matching the current locale. Prevents duplicate-content SEO penalties.
- **Per-locale sitemap.xml.** Replace the Phase 1 static sitemap with a generated one that lists every page × every locale with `hreflang` alternates. This is the minimum for SEO to crawl the non-English content correctly.
- **Cookie consent banner.** Required for GDPR/LGPD compliance. Use a minimal consent library (e.g. `react-cookie-consent` or hand-written) with two tiers: necessary (always on) and analytics (opt-in). Link to the privacy page. Consent state persists to localStorage. Banner appears only once.
- **Analytics scaffold (privacy-respecting).** Wire up Plausible or Umami (both are privacy-first, GDPR/LGPD-compliant, no cookies needed). Track: page views, locale selection, CTA button clicks, docs section navigation. Do NOT use Google Analytics — it triggers cookie consent requirements and complicates LGPD posture. The specific provider is operator's choice, but the scaffold should make it a one-line config change.
- **Image optimization.** Audit `public/screenshots/*.png` for file size. Any > 200KB gets re-encoded as WebP with a PNG fallback via `<picture>`. Goal: total hero page weight < 1MB on first load. The 6 dashboard screenshots are the biggest offenders.
- **Preconnect / preload hints.** Add `<link rel="preconnect" href="https://fonts.googleapis.com">` and `<link rel="preload" as="image" href="/hero.png">` (or whichever is the LCP image) to `index.html` for faster first paint.
- **Performance budget.** Add a Lighthouse CI check that fails if the Landing page scores below 85 Performance or below 90 SEO. Run on every PR in the Vite project's CI (or manually via `npx lighthouse http://192.168.2.60:3000 --only-categories=performance,seo` for now).
- **Legal page content.** Privacy policy and terms of service get real content (not "Coming soon"). This is the only part of Track E that BLOCKS on operator input — the other items can ship with placeholders and be filled in later. The Privacy Policy specifically needs LGPD-aware language for Brazilian users.
- **Pricing page strategy.** Either (a) wire a real pricing page with a "Free / Pro / Enterprise" scaffold and "Contact sales" for Enterprise, or (b) remove the pricing link from Phase 1's stub. Operator picks one during Phase 2 kickoff.

**Duration:** ~3 days of PF work for the structural items, plus ~1 day blocked on operator for legal content. Parallelizable with Track A (i18n), because the Track E metadata work feeds directly into the locale JSON files that Track A is already editing.

### Phase 2 acceptance gate
- QA-Unit runs `validate-i18n.spec.js` and all 12 locale/page combos pass.
- QA-Unit runs the new Playwright anchor-phrase test and 0 English phrases leak in non-EN locales.
- QA-E2E clicks every sidebar link and confirms non-stub content on the right-hand pane.
- Architect reviews Security card copy for RFP-readiness.
- **[NEW in v1.3] Lighthouse Performance ≥ 85, SEO ≥ 90, Best Practices ≥ 90 on all 3 pages.**
- **[NEW in v1.3] OG tags render correctly** in the [Meta Sharing Debugger](https://developers.facebook.com/tools/debug/) and Twitter Card Validator for all 3 pages in all 4 locales.
- **[NEW in v1.3] Cookie consent banner appears on first visit, persists across reloads, and does not re-appear after dismissal.**
- Supervisor approves → v0.7 tag.

---

## Phase 3 — Polish (correctness) + Design pass (v0.8, 4–5 days)

**Theme:** Kill every finding in the audit's MEDIUM tier (Tracks A-E = correctness). Run one bounded design pass (Track F = design judgment). Codex (v1.3) correctly flagged that the v1.2 version of this phase mixed correctness and design-judgment work under a single "just polish" label — v1.3 separates them into two gates so the dishonest scope labeling is gone.

### Track A — Motion & animation correctness
- Add `motion.config` wrapper (or per-component `useReducedMotion` hook) so Framer Motion honors `prefers-reduced-motion`. Reference: framer-motion docs on reduced motion.
- Rewire Arc Connector in How-it-Works to be scroll-triggered, not mount-triggered. Use the existing `ScrollReveal` component as the wrapper.
- Add responsive hide rule for Arc Connector on mobile (`hidden md:block` on the containing wrapper).
- Fix hero stagger to the plan's 7-element irregular pattern (0/100/250/400/500/700 ms delays). Add the missing 7th element (the trust line is currently inside another wrapper — promote it).

### Track B — Responsive breakpoints
- **Docs sidebar breakpoint fix**: show sidebar at `md:` (768px), hide TOC at `md:` — plan responsive table lines 391–421. Current implementation waits until `lg:` (1024px).
- **[HIGH — upgraded from MEDIUM in v1.1]** **Mobile hamburger for docs**: add an actual toggle button in the docs header that slides the sidebar in from the left as a drawer. Reuse state pattern from any existing mobile-nav code if present. **Severity upgraded:** the visual audit (agent 4) confirmed that at both 375×812 and 768×1024 there is no visible trigger at all, which means mobile and tablet users are **functionally locked out** of docs navigation — they can only see whichever single section loads on initial render. This is not a "small tap-target polish" issue; it's a broken navigation surface on every non-desktop viewport and must land in Phase 1 or early Phase 3 at the latest.
- **Language button tap target**: bump to `min-h-[44px] min-w-[44px]` with appropriate padding. All 3 pages, both Navigation and Footer.

### Track C — Typography fixes
- **Docs H1/H2/H3 upsize** to match plan (48/40/32 px at desktop, responsive down from there).
- **Code blocks use JetBrains Mono**: add `font-mono` class to every inline `<code>` and `<pre>` in DocsPage. Verify via computed style in browser.
- **StatCard numbers upsize** from `text-3xl lg:text-4xl` to `text-5xl lg:text-6xl` to hit the plan's 48px.

### Track D — Docs interaction quality
- **Auto-generated TOC**: replace the static `tocItems` array with a `useEffect` that scans `document.querySelectorAll('h2[id], h3[id]')` and builds the TOC at mount.
- **Scroll-spy**: add `IntersectionObserver` to highlight the current section as the user scrolls. Use the existing active-state styling.
- **[EXPANDED in v1.1]** **Code blocks need full visual treatment, not just a font change.** The visual audit (agent 3) saw that code examples on the docs page were indistinguishable from body prose — `font-mono` inheritance via CSS was the only distinction, and that's not enough to read as "this is code." The fix is a stack, not a single change:
  1. Wrap every code example in a `<pre>` with an explicit dark background (`bg-slate-900`), padding (`p-4`), rounded corners (`rounded-lg`), and a subtle border (`border border-slate-800`).
  2. Apply `font-mono` explicitly on the `<code>` element (not just inherit — the earlier typography audit found `font-mono` missing).
  3. Add `shiki` (build-time) or `prism-react-renderer` (runtime) for syntax highlighting. Prefer `shiki` to keep runtime cost zero.
  4. Add a `Copy` icon button in the top-right of every `<pre>`. Clipboard API is trivial; the button is more about signaling "this is code" visually.
  5. Inline `<code>` in prose gets a lighter treatment: `bg-slate-100 px-1.5 py-0.5 rounded text-sm`. Distinct from block code but still visually differentiated from prose.
- **Sidebar search**: wire the existing input to client-side fuzzy search (`fuse.js` or a hand-written substring match) over the page headings. Keyboard navigation (↑/↓/Enter).

### Track E — Visual correctness (NEW in v1.1, expanded in v1.2, split from design-pass items in v1.3)

Findings in this track are **correctness issues** — the CSS is wrong, the effect doesn't render, the font isn't applied, the architecture is fragile. They can be fixed deterministically with no design judgment required. Design-judgment items (card variation, hierarchy cascade, docs color rhythm) moved to the new **Track F — Design pass** below, which Codex correctly flagged as not "polish" but a redesign surface.

- **Frosted glass is cosmetically broken on the docs page.** Source audit reported `bg-white/95 backdrop-blur-md` as correctly applied; visual audit saw flat white cards with no blur and no translucency. Root cause: `backdrop-blur` only does anything when there's content behind the element to blur, and the docs layout has a solid light-gray page background with no overlapping content under the cards, so the blur has nothing to operate on, and the `/95` opacity makes the card look 100% opaque. Fix options (pick one):
  1. **Add a patterned/gradient page background** behind the 3-column layout — a soft mesh gradient (`bg-gradient-to-br from-slate-50 via-blue-50/40 to-teal-50/30`) or an SVG noise pattern. This gives the `backdrop-blur` something to blur against and brings the aesthetic alive.
  2. **Drop to `/70` opacity** on the cards and add `backdrop-filter: blur(24px) saturate(1.4)` explicitly, combined with `isolation: isolate` on each card container, so the effect actually composites.
  3. **Accept that pure docs pages don't benefit from frosted glass** and replace the effect with solid cards + `shadow-md` for the docs-specific look. This is the YAGNI answer and the plan should allow it if options 1–2 are too much work.
- **Architect picks the approach during Phase 3 kickoff** and documents the choice in the plan doc's Appendix before implementation.
- **Visual regression baseline:** once the frosted glass approach is locked, `qa-screenshot-validation.mjs` needs a fresh baseline captured at natural desktop viewport (no artificial `resize_page` calls) on all 3 pages in all 4 locales so that Phase 3 QA-E2E has something to regress against.
- **Invisible-heading P0 from Phase 1 Track C** is the other big visual-only defect; it's already in Phase 1 because it's the highest-severity fix. Listed here too so the full Track E has visibility of both bugs as a set.

**[NEW in v1.2] Docs type rhythm — tighten body line-height.** The designer review noted a "floating text effect" from overly loose line-height in docs body copy. Fix: audit the current body `line-height` value in `index.css` (currently `28px` on 16px body = 1.75 ratio, confirmed overly loose) and tighten to ~1.55–1.6 (26px on 16px). Headings stay at their current 1.2 line-height. This change alone makes paragraphs feel grounded rather than drifting, and closes the "docs feel visually inert" critique that the designer review flagged. Correctness, not taste — this is a measurable ratio change.

**[NEW in v1.3] Global color architecture refactor.** Codex surfaced a broader problem than the single invisible-heading bug: the pattern `body { color: var(--gray-600) }` + `h1-h6 { color: var(--navy) }` declared globally in `index.css:52-65` is fragile in a utility-first Tailwind codebase. Every dark-surface section has to fight the global default; the Phase 1 Track C fix (add `text-white` directly to every dark-surface heading) is a patch, not a cure. The structural fix that Phase 3 can afford: **remove color declarations from global element selectors entirely**. Replace with either (a) a scoped `.prose` wrapper around content regions that needs navy text, or (b) component-level `text-navy-900` / `text-white` utilities on each heading and paragraph. The heading `font-family`, `font-weight`, and `line-height` can stay global — only the `color` declaration moves out. Verify: after the refactor, every heading on every page still renders the correct color, and adding a new dark-surface section in the future will not require a collateral heading patch. This is the "better structural fix" promised in the v1.3 header note.

### Track F — Design pass (NEW in v1.3, split from Track E)

These items are **design judgment calls**, not correctness fixes. They were moved out of Track E because Codex correctly flagged that labeling Phase 3 "just polish" while including design-pass work was dishonest scope management. Track F is an explicit, bounded design pass that the Architect owns end-to-end, with a separate design-review gate.

- **Feature card variation — kill the Tailwind-uniform look.** (From the v1.2 designer craft review.) The 6 Landing feature cards are currently "identical rounded-xl corners, identical light-blue icon backgrounds, identical card shadows, identical padding, identical heading size — this is Tailwind defaults applied uniformly." Uniformity reads as template fill, not intentional design. Introduce **deliberate variation** across the 6 cards in at least two of these axes:
  1. **Depth variation** — one or two cards pop forward with a tighter, darker shadow (`shadow-xl` vs `shadow-md`); others recede with softer edges. Creates a visual rhythm where the eye moves between foreground and background.
  2. **Icon treatment variation** — not every card has the same pastel-bg icon puck. One card can have a larger line-icon, another a filled icon with a colored background, another an emoji, another a numbered marker. Vercel's feature cards do this well.
  3. **Accent color variation** — sparingly rotate which cards pull which color from the palette. Not every card should anchor to teal; let one pull purple, let one stay neutral navy. The palette exists (teal / purple / pastel-blue / pastel-yellow / pastel-green / pastel-pink) — use it.
  4. **Background variation** — at least one card can have a subtle gradient or tinted background instead of pure white. Linear's landing page uses this trick.
  The goal is **three or four intentional differences across the grid**, not randomness. Architect picks the specific treatment during Phase 3 kickoff.

- **Hierarchy cascade verification.** (From the v1.2 designer craft review.) The designer review pointed out that the invisible H1 doesn't fail in isolation — it collapses the whole page's hierarchy, so subheading and body text all read at the same visual weight. The Phase 1 Track C fix addresses the contrast bug; this Track F item verifies that the restored hierarchy actually reads: H1 must be visibly **the** focal point of the hero (roughly 1.5× the subheading's optical weight, 3× the body's), with enough whitespace around it that a first-time visitor's eye anchors on it in under 1 second. This is a design judgment call — Architect approves visually at the Phase 3 design-pass gate. Similar check applies to Product page H1 and every Docs page H1.

- **Docs color rhythm upgrade.** (From the v1.2 designer craft review.) The designer review called the docs main content area "color-quiet to a fault — white-on-gray prose with color only in the sidebar and right-column links." Introduce color into the content stream at two specific touch points:
  1. **Callout boxes** — info / tip / warning / note boxes with colored left borders and tinted backgrounds. The CSS tokens already exist in `index.css` (`--docs-info`, `--docs-warning`, `--docs-tip`); they're just not used in the content. Wire them into reusable `<Callout type="info">` components and scatter them through every docs page where a pull-quote, warning, or tip makes sense.
  2. **Teal-accented code blocks** — in conjunction with Track D's code block treatment, give inline `<code>` a subtle teal tint (`text-teal-700` or `bg-teal-50 border border-teal-100`) to make it feel connected to the primary action color. Block code stays dark-navy-on-white but the inline code ties into the palette.
  The goal is to break the white-on-gray prose stream into rhythmic segments the eye can skim, without overwhelming the reader or making the docs feel decorated. Restraint is the rule — 1–2 callouts per page average, not 6.

### Phase 3 design-pass gate (Track F)

- Architect reviews the Landing feature grid for intentional variation and posts PASS or NEEDS REVISION. 6 identical cards = fail.
- Architect verifies the H1 hierarchy cascade reads correctly at 375/768/1440 on all 3 pages, all 4 locales. First-visitor eye must land on the H1 within 1 second of page load (Architect self-tests by looking and reacting).
- Docs main content shows callout boxes and teal-accented inline code consistently, with restraint (1-2 callouts per page average).
- The design-pass gate is **separate from the correctness gate** — Track E can pass while Track F still has open revisions, and vice versa. Both must pass for v0.8.

### Phase 3 acceptance gate
- QA-E2E runs the responsive suite at 375/768/1024/1440 on all 3 pages in all 4 locales. Zero overflow, zero overlap, all tap targets ≥ 44px.
- `prefers-reduced-motion` honored: Chrome DevTools emulation → all motion collapses to instant/opacity-only.
- Architect verifies the Arc Connector behavior on scroll (not mount) and mobile-hidden.
- **[NEW in v1.1]** **Mandatory visual walkthrough:** Architect takes Chrome-DevTools screenshots at 375 / 768 / 1440 natural on all 3 pages × all 4 locales = 36 screenshots, and eyeballs every one for legibility (no invisible text), layout integrity (no overlap, no overflow), visual effect rendering (frosted glass actually frosted, code blocks actually code-like), and content completeness (no empty sections with whitespace voids). This step is explicitly required because the v1.0 audit demonstrated that DOM-based QA is blind to contrast failures, cosmetic class-vs-render mismatches, and empty-section voids. No purely-DOM QA replaces this gate.
- **[NEW in v1.1]** Mobile/tablet docs navigation is functional: tapping the hamburger opens the sidebar drawer; every section is reachable.
- Supervisor approves → v0.8 tag.

---

## Phase 4 — Content production (v1.0, gated on external)

**Theme:** Replace every placeholder with real content. This is not a coding task — it is a content production task, and most of it blocks on inputs the Dev Squad cannot generate.

1. **90-second demo video** — record, edit, encode (H.264 + WebM), embed. Host locally or on a CDN. Dev Squad can build the `<video>` component; operator must supply the actual video.
2. **Real customer testimonials** — minimum 2, ideally 4. Real names, real titles, real companies, real photos (or approved avatars). Operator or Supervisor must source. Until then, Social Proof section stays dormant.
3. **Real customer logos** — minimum 3. Operator must source or approve generic industry placeholders. Until then, Logo Bar stays dormant.
4. **Real stats numbers** — replace the placeholder `1,200+ teams / 45K+ tasks / 98% retention / 5min setup` with verified numbers. Or, per the plan's product-quality narrative, swap the entire stats block to `94% tasks on time / 3min capture-to-board / 0 extra apps / 12+ languages` (the plan's original spec, which is self-verifiable).
5. **SOC 2 / compliance posture** — real answer from the operator, as noted in Phase 2 Track B.

Phase 4 is **event-driven**: each item unblocks independently. PF opens a new PR for each content drop as inputs arrive.

---

## 4. Ownership matrix

| Track | Primary | Reviewer | Notes |
|---|---|---|---|
| All JSX / component work | Programmer-Frontend | Architect | Single owner — this is a pure frontend project |
| i18n locale JSON translation | Programmer-Frontend | Architect | PF drafts, Architect reviews language quality |
| API Reference inventory | Programmer-Backend | Architect | PB produces endpoint list; PF wires into DocsPage |
| Admin & Deployment docs | DevOps | Architect | DevOps owns the deployment story |
| Security & Data copy | BLOCKED on operator | Architect | Real posture must come from operator |
| Content production (video, logos, testimonials) | BLOCKED on operator | Supervisor | Gated externals |
| QA — i18n | QA-Unit | — | Uses existing `validate-i18n.spec.js` |
| QA — responsive & visual | QA-E2E | — | Uses `qa-screenshot-validation.mjs` + Playwright |
| Coordination + phase gates | Supervisor | — | Approves each phase's merge |
| Final visual pass | Architect | — | Signs off each phase tag |

## 5. Definition of "done" per phase

- **Phase 1 gate:** Architect screenshot PASS on all 3 pages at 1440px in all 4 locales. Every heading legible. Zero console errors. Plan compliance matrix shows 0 missing structural sections on Landing.
- **Phase 2 gate:** `validate-i18n.spec.js` passes 12/12. Security section reviewed by operator. Docs sidebar has non-stub content for every link.
- **Phase 3 gate:** QA-E2E full 4×3 responsive run PASS. Lighthouse a11y score ≥ 95 on all 3 pages. `prefers-reduced-motion` emulation test PASS.
- **Phase 4 gate:** Operator sign-off. No automated gate — this phase is content, not code.

## 6. Build discipline (non-negotiable for all phases)

- **i18n rule:** after Phase 2, **zero hardcoded user-visible strings** outside of `src/components/animations/`. Add a Playwright spec that fails if any `<h1>-<h6>`, `<button>`, or `<p>` matches a known English pattern without a `data-i18n-ok` attribute.
- **Locale symmetry rule:** `en.json` and the other 3 locale files must always have matching key counts. Add a pre-commit hook / CI check comparing key counts.
- **No new dead code:** the existing `App.css` references to undefined `--accent-*` variables should be deleted during Phase 3, not carried forward.
- **Commit cadence:** one commit per track, not one per finding. Makes review tractable.

## 7. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Security track blocks Phase 2 waiting for operator input | Phase 2 slips 1–3 days | Start other Phase 2 tracks in parallel; security can land separately |
| i18n translation quality regresses when 500 strings are added | Non-English users see awkward copy | Architect reviews every locale file before merge, not just the JSX |
| PB's API endpoint inventory is incomplete or changes | Docs API Reference is out of date on merge | Generate inventory from the FastAPI OpenAPI spec (auto-sync), not hand-written |
| Phase 3 motion reduced-motion fixes accidentally break desktop animations | Visual regression | QA-E2E includes both `prefers-reduced-motion: reduce` and `: no-preference` runs |
| Content Production phase never completes (external gated forever) | v1.0 ships with placeholders | Accept that v0.8 is the "engineering done" release, v1.0 is a content milestone |
| Dev Squad gets pulled onto another board mid-plan | Schedule stretches | Supervisor reserves PF for at least Phase 1 + Phase 3 (structural work only PF can do); content tracks in Phase 2 can slip |

## 8. Success metrics (post-v0.8)

- **Translation coverage:** 100% of user-visible strings are `t()`-wrapped, verified by lint + Playwright
- **Plan compliance:** 0 `MISSING` rows in a re-run of the plan-compliance matrix agent
- **a11y:** Lighthouse score ≥ 95 on all 3 pages, 0 form-field warnings, all tap targets ≥ 44px
- **Visual regression:** `qa-screenshot-validation.mjs` passes with ≤ 2% pixel delta vs a sign-off baseline
- **Motion:** `prefers-reduced-motion` emulation → no transforms, only opacity fades
- **Government RFP dry-run:** a mock RFP review finds at least the top 6 compliance items (encryption x2, residency, RBAC, audit, retention) answered without "Contact us"

## 9. First action

1. Commit this plan to `docs/plans/2026-04-14-taskflow-web-presence-evolve-plan.md`.
2. Post the Phase 1 task breakdown to the Supervisor via MC API.
3. Supervisor assigns to Programmer-Frontend with Architect as the reviewer.
4. Architect gate at the end of Phase 1 → if green → same cycle repeats for Phase 2 after Track B's security input is unblocked.

Alternatively, if scope must be trimmed: **Phase 1 alone** is ~2 days of PF work and would already lift the site from a 3/10 commercial grade to ~5/10 — it's the highest-leverage slice, and the P0 invisible-heading fix alone takes the site from "broken" to "at least readable."

---

## 10. Methodology note — why a visual audit layer is non-negotiable (added in v1.1)

The v1.0 draft of this plan was built from a 10-subagent audit that combined source-reading agents (reading JSX/CSS files) and two browser agents (driving Chrome DevTools MCP). Both layers were thorough at what they did. Both missed the same class of defect: **things that only manifest in the rendered pixels**.

After v1.0 was committed, a 4-agent pure-visual audit ran — each agent opened the live site in Chrome MCP, took full-page screenshots, and actually looked at the resulting images (the screenshots were passed back into the agent's context, so the agent was reasoning from pixels, not from the DOM). That audit found three defects the earlier layer missed and one defect the earlier layer had mislabeled:

1. **Invisible Landing hero H1** (navy-on-navy, confirmed across all 4 locales). The source/DOM agents took screenshots and queried `getBoundingClientRect` — they never compared foreground color against background color, and they never read the text on the image to see whether it was legible. The visual agent saw the bug immediately and confirmed it in all 4 locales.
2. **Frosted glass on docs is cosmetically flat.** The source audit marked frosted glass as ✓ because `bg-white/95 backdrop-blur-md` was present in the JSX. The visual audit saw flat white cards — correct at the class level, wrong at the effect level. This is a class of bug that cannot be found by grep.
3. **Docs code blocks are visually indistinguishable from body prose.** The source audit noted "no syntax highlighting library imported" as a minor issue. The visual audit confirmed that the visual experience is actually "I can't tell this is code" — which is much more severe than "missing Prism." A `font-mono` inheritance alone is insufficient when there is no background, no border, no padding, and no color differentiation.
4. **Mobile docs is a dead end.** The source audit correctly flagged that there was no hamburger trigger, but scored it MEDIUM. The visual audit confirmed that means mobile and tablet users have no way at all to navigate the docs — the correct severity is HIGH.

**What this means for every future QA pass on this site (and any other UI product):**

- **A screenshot-reading layer is required on top of every DOM-level QA pass.** Computed-style queries, `getComputedStyle`, `getBoundingClientRect`, and even `console.warn` checks are blind to rendered-pixel reality. An agent that takes screenshots and describes them in natural language catches bugs DOM assertions cannot.
- **The two layers are complementary, not redundant.** Source/DOM audits are fast, exhaustive, and enumerable — 30 findings in 10 minutes. Visual audits are slower and fewer findings per pass but catch an entirely different bug class. Skip either layer and you miss things.
- **"Classes are present" ≠ "effect renders."** The frosted-glass bug is the canonical example: ship-ready-looking source code, ship-broken-looking website. Always verify visual effects visually. This extends to shadows, gradients, backdrop filters, mix-blend modes, and any CSS feature where rendering depends on GPU compositing or context.
- **Contrast checking is a screenshot-reading problem, not a DOM problem.** Tools like axe-core can flag computed contrast ratios, but they only catch simple cases. A visual agent reading the image catches the actual legibility question: "can a human being read this?" A 4.5:1 ratio that technically passes WCAG can still be unreadable in practice if the font weight and size are wrong. Human eyes (or an agent simulating them on an image) are the ground truth.
- **Locale-switched screenshots are mandatory.** Many bugs only manifest in non-default locales — text length overflow, missing translations leaving English in Portuguese pages, layout collapse from longer German words. The visual audit found the Product and Docs pages render 100% English regardless of locale setting; that matches the source audit's zero-`t()` finding but proves it visually for reviewers who don't read source.
- **For this project specifically:** the Phase 3 gate includes a mandatory 36-screenshot visual walkthrough (3 pages × 4 locales × 3 viewports). If that count feels high, reduce viewports first — not locales, not pages. Every page × every locale matters because bugs hide in combinations.

**Building this into the Dev Squad workflow:** QA-E2E's `qa-screenshot-validation.mjs` already takes screenshots. What's missing is a step that feeds those screenshots back to an agent with an explicit "describe what you see, flag anything that looks broken" prompt. Adding that step — which is roughly one Chrome-DevTools-MCP call per screenshot — converts existing screenshot capture into a visual-reasoning gate without new tooling. Recommend adding this as a sub-step to the Phase 3 QA-E2E acceptance flow: capture → describe → flag → gate.

### 10.1 Role design for LLM-agent audits — what works and what doesn't (added in v1.2)

After the 4-agent visual audit in v1.1, a 3-agent human-perspective audit was run to catch the composition, convention, and craft-sensibility gaps that even the visual agents missed. The 3 agents were: (a) naive first-time visitor, (b) skeptical buyer memo, (c) senior designer craft critic. Only (b) and (c) worked. (a) failed in a way that matters for every future audit in this repo.

**Why the naive first-time visitor role failed.** Two failure modes stacked on top of each other:

1. **LLM vision reads text a human can't.** The naive-visitor agent was told to look at screenshots with "average human vision in a bright office" and report honestly. It saw the Landing hero screenshot, recognized the French headline "WhatsApp est désormais votre chef de projet" via character-level OCR-style vision, and reported the text as "bold and readable" — exactly contradicting the independently confirmed navy-on-navy invisibility bug and the operator's own screenshot-eye test. The model's vision is effectively superhuman on text recognition; it parses what human eyes cannot read at arm's length. Asking a model to "pretend you have average vision" does not actually degrade its vision.

2. **The polite-reviewer default.** Under first-impression framing ("what do you think of this site?"), the model defaults to the tone of a friend reviewing a friend's portfolio — reassuring, positive, generous with "feels legitimate" and "well-executed" phrases. Explicit instructions to "be blunt, don't hedge, no marketing language" did not override the default. The naive-visitor prompt does not carry enough adversarial friction to break past it.

Net result: agent 1 filed a glowing review claiming "TaskFlow feels like a legitimate, well-executed product" and flagged zero known bugs, despite having the same screenshots in its context that the skeptical-buyer and designer-critic agents correctly read as broken.

**What worked instead.** The two other roles, both run after agent 1 failed:

- **Skeptical-buyer memo.** Framed as a private internal memo to a decision file, with adversarial priors (a known-bug list the agent had to verify against the rendered page). The memo voice gave the agent permission to be blunt, and the adversarial priors forced it to confirm or refute specific defects rather than writing a free-form review. Output: correctly verified all 12 known bugs, framed "integrations" as bait-and-switch, called the i18n "localization theater," concluded "would not shortlist."
- **Senior-designer craft critic.** Framed as a portfolio review from a designer with named references (Linear, Stripe, Vercel, Figma). The expert framing gave the agent permission to be critical, and the required-named-comparables rule forced it to anchor every claim in specific visible evidence. Output: confirmed known bugs AND surfaced four new craft-level issues (feature card uniformity, hierarchy cascade, docs color quietness, docs line-height) that no prior agent caught.

**Guidelines for any future LLM-agent UI audit in this repo (softened in v1.3 after Codex pushback that the v1.2 version was overfit from one failure sample):**

1. **Use the naive first-time visitor role with caution.** The one failure sample that produced v1.2's "never use this role" rule is real, but one sample is not enough to ban the role globally. Safer posture: **the naive-visitor role requires adversarial priors to work.** Without a known-bug list the agent must verify against, the polite-reviewer default wins every time. With adversarial priors, it can be a useful first-impression sanity check. Do not use it as the only layer of audit, and do not trust its positive findings without independent verification.
2. **Use adversarial-buyer memo framing for content/trust audits.** Supply a known-bug priors list when one exists. Require the agent to verify or refute the priors specifically. Frame the output as private decision-file notes, not as a public review. This was the v1.2 finding and Codex did not push back on it.
3. **Use senior-designer critic framing for visual craft audits.** Require named comparables (Linear, Stripe, Vercel, Figma). Prohibit generic marketing language ("modern," "clean," "professional") unless immediately followed by a specific visible example. Give the agent explicit permission to be critical.
4. **Adversarial-buyer and designer-critic roles are required for large UI audits, overkill for small iterative fixes.** For a comprehensive site audit like this one, both roles add signal the defect-hunting layer misses. For a 1-component bug fix or a copy change, they are process theater and you should skip them. Calibrate the role count to the audit scope.
5. **Neither role replaces the pure-visual audit layer** (screenshots + eyes-on-pixels element-by-element). For large audits, all three layers — DOM/source, pure-visual, human-perspective — are complementary and should run together.
6. **Contrast judgment rule (revised in v1.3 — measurable, not eyeball).** The v1.2 version of this rule said "if text is within 2 shades of its background in luminance, report it as hard to read." Codex correctly noted this is subjective without formalization. The revised rule: **use a measurable WCAG contrast ratio check as ground truth.** The threshold is **4.5:1 for body text and 3:1 for large display text (≥18pt or ≥14pt bold)** — the standard WCAG AA minimums. Any text below those ratios is reported as a contrast failure regardless of whether the agent's vision can recognize the characters. The ratio is computable from the rendered pixel colors via `getComputedStyle` or a simple canvas-based sample — no eyeball estimation required. If the agent lacks a way to compute the ratio, the fallback rule is: **screenshot the element, zoom it 4x, and ask "can a human squinting in a bright office read this without effort?" If no, report it.**
7. **No "feels" language without evidence.** "Feels legitimate" / "feels unfinished" / "feels rushed" are banned unless followed by a specific visible observation. This rule alone suppresses most of the polite-reviewer default tone.
8. **State the target codebase explicitly when spawning an audit agent or asking an external tool (Codex, etc.) to review.** Codex's v1.3 first-round review failed its critique #1 because the plan never identified the target as the external Vite project on `.60`, so Codex went looking in the openclaw-mission-control repo and audited the wrong thing. This was a plan-documentation failure, not a Codex failure. Any plan that describes code in a different location than the plan file itself must say so explicitly at the top.

This note exists so that the next time someone audits TaskFlow (or any other UI project in this repo), they start with the right layering: source audit first for breadth, visual audit second for truth, human-perspective audit third for judgment — with adversarial-buyer and designer-critic roles for large audits, calibrated down for small ones, and with measurable contrast checks instead of eyeball estimation.
