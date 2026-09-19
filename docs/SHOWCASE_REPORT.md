# Company Showcase Report

Date: 2026-09-20

## Scope

Two static company-showcase sections were added immediately before the blog in
both `templates/index.html` and `templates/welcome.html`. They are shared
partials, so the markup is defined once in
`templates/partials/showcase_vision.html` and
`templates/partials/showcase_collab.html`.

## Reference design extraction

Source: `docs/reference/js-hub-2.html`.

| Token or rule | Exact reference value | Source lines |
| --- | --- | --- |
| Font loading | Google Fonts: Urbanist 500/600/700/800, Work Sans 400/500/600, Space Mono 400/700, `display=swap` | 7–8 |
| Core palette | `#0A0F0D`, `#111815`, `#161F1B`, `#243029`, `#1B241F`, `#EAF2EE`, `#8FA69C`, `#55685F` | 12–19 |
| Accent colours | amber `#F7DF1E`; teal `#2DD4BF`; teal dim `#0F5E54` | 20–23 |
| Fonts | display `Urbanist`; body `Work Sans`; mono `Space Mono` | 27–29 |
| Shadows | `0 26px 64px -26px rgba(0,0,0,.75)` and `0 8px 22px -12px rgba(0,0,0,.55)` | 30–31 |
| Body treatment | dark background, 1px radial-dot pattern at `28px 28px`, fixed amber/teal radial glow | 36–49 |
| Display heading | `clamp(21px,2.8vw,28px)`, Urbanist 700, `-.01em` tracking | 127–128 |
| Eyebrow | Space Mono, 11px, teal, `.14em`, uppercase | 79–82 |
| Section container | `max-width:1140px`; `64px 26px` spacing; border top | 113–114 |
| Card | panel background, 1px border, 13px radius, 22px padding; hover border `#33453C` and small shadow | 345–346 |
| Project heading/body | 17px Urbanist 700 / 13px dim body | 349–351 |
| Pill | mono 10px; teal translucent background; teal-dim border; 20px radius; `3px 10px` padding | 353 |
| Primary button behavior | mono 11.5px, amber, 6px radius, `7px 13px`, brightness/lift/shadow hover | 167–172 |
| Focus ring | 2px teal, 2px offset, 3px radius | 54 |

The reference uses `body::before` and `body::after`. To avoid modifying the
host pages’ global body styles, their exact dot-pattern and glow values are
applied through `.showcase-scope` and `.showcase-scope::before` only.

## Files changed

- `static/css/showcase.css` — scoped reference tokens, responsive zig-zag
  layout, link focus treatment, and reduced-motion behavior.
- `templates/partials/showcase_vision.html` and
  `templates/partials/showcase_collab.html` — static shared section markup.
- `templates/index.html`, `templates/welcome.html` — load the reference fonts
  and scoped CSS, then include both partials before the blog.
- `mysite/core/tests.py` — rendering, order, link-security, and zero-query
  template-render coverage.

## Link inventory

| Product | Final href | Reachable from this environment |
| --- | --- | --- |
| Lomi Marketplace | `https://lomi-apple.onrender.com` | Reachable — HTTP 200 from a HEAD request. |
| Speakora | `https://speakora-ghfw.onrender.com/` | Reachable — HTTP 200 from a HEAD request. |
| DFIX Music Platform | `https://stanslau2025.github.io/DFIX_DGXcore` | Reachable — HTTP 200 from a HEAD request. |
| HOTBANDO | `https://34.35.47.111:3000` | Unreachable — HTTPS connection timed out after 20 seconds; raw-IP HTTPS may also show a certificate warning. |
| MauzoSheet AI | `https://www.mauzosheetai.co.tz/` | Reachable — HTTP 200 from a HEAD request. |
| ZIMA Solutions | `https://zima.co.tz/` | Reachable — HTTP 200 from a HEAD request. |
| BLACKSCIENCE Technologies | No link by requirement | Not applicable |

The live reference page returned HTTP 200 to a read-only HEAD request, but was
not accessible through the available browser fetcher. The saved reference
source in `docs/reference/js-hub-2.html` was used as the exact token source
instead.

## Content to confirm

- BLACKSCIENCE Technologies attribution for MauzoSheet AI.
- HOTBANDO WiFi-management feature claims.
- HTTPS certificate behavior for the supplied raw-IP HOTBANDO URL.
- Whether Speakora currently shows a Supabase setup notice to visitors.

## Deviations

- None to the supplied content or links. The body pseudo-element decoration
  is scoped to the new sections rather than the host document body, as required
  to avoid changing existing page styling.
- Playwright/browser screenshots at 360px, 768px, and 1280px: **NOT RUN**.
  No Playwright executable or installed local package was available.
