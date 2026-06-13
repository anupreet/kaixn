# kaixn marketing site — session history (2026-06-13)

A shareable log of the marketing-website work. It all happened on **2026-06-13**,
across a chain of sessions from ~11:57 to ~14:41 (local time, America/Los_Angeles).

## The original goal (where it all started)

First prompt of the effort — session `010f1ab7`, **11:57**:

> **"you are building a public facing website for Kaixn. You have access to the repo,
> which has docs, please read them to create a marketing website that allows people to
> access it and be able to sign up. we want webflow to be the CMS. You have access to
> the AWS personal profile. https://webflow.com/dashboard/workspace/kaixn-7002b0"**

Immediate follow-up that shaped the positioning:

> **"I want you to act like PMM and think through the real value prop here — read the
> docs, and not the words… see how we should be positioning it. this is how we want the
> future of software development to look like"**

## Today's session timeline (all 2026-06-13, local time)

| Time | Session | Kickoff prompt |
|---|---|---|
| 11:57–12:12 | `010f1ab7` | "you are building a public facing website for Kaixn…" *(original goal)* |
| 12:12–12:13 | `c6d27373` | "continue the Kaixn marketing site" |
| 12:14–12:21 | `63a6b90a` | "continue the Kaixn marketing site" |
| 12:22–13:33 | `ebe72222` | "continue the marketing site" |
| 14:01–14:41 | `368c0636` | "now continue with the marketing website goal" *(this session)* |

*(Parallel sessions on the engineering handbook / product flow ran the same day —
`587d0b4d`, `a0d58888`, `67ac9250`, `be4830aa` — but are outside this marketing log.)*

## This session's goal prompt

> **"now continue with the marketing website goal"**  *(session `368c0636`, 14:01)*
> *(`/mcp` was run first to authenticate the Webflow connector.)*

It was the closing stretch of the original brief — wiring sign-up, the logo, and login
routing, then publishing. Early in the session a decision was locked:

> **Production site = Webflow** (chosen over deploying the static `marketing/index.html`
> build to AWS/S3).

## How the work maps back to the original goal

| Original ask | Status |
|---|---|
| Public-facing marketing website | ✅ Built & published (`kaixn.webflow.io`) |
| Let people **sign up** | ✅ Waitlist form → `app.kaixn.com/api/waitlist` (live-verify pending — Dope Security blocks it from the work machine) |
| Let people **access it** | ✅ "Log in" (nav + footer) → `app.kaixn.com` |
| **Webflow** as the platform/CMS | ✅ Webflow chosen as production |
| AWS personal profile | ✅ Backend lives there; waitlist persists to it |
| PMM positioning ("future of software dev") | ✅ Hero locked: *"Agents write the code. You govern the decisions."* + 8-section copy deck |

## Site facts

| | |
|---|---|
| Webflow site | `kaixn` (`6a2da87e2e5eab84c1a3aa98`) |
| Home page | `6a2da8802e5eab84c1a3aa9c` |
| Staging URL | https://kaixn.webflow.io |
| App / "Log in" target | https://app.kaixn.com |
| Plan | Free tier (constrains custom code + custom domain) |

## What was done

### 1. Connector + account verified
The Webflow MCP connector was confirmed authenticated under the **personal** account
(the `kaixn` workspace is visible — no longer the unrelated "Brevian" account that
previously blocked everything).

### 2. Waitlist form wired to the backend ✅ (published)
- Form `action` → `https://app.kaixn.com/api/waitlist`
- Form `method` → `post`
- Email input `name` renamed `field` → `email` (matches the backend's urlencoded
  parser at `src/kaixn/web.py:186`)
- Native submit path: backend 303-redirects to `kaixn.com/?waitlist=ok`

### 3. Ensō logo added to the nav ✅ (published)
- Inserted a real Webflow **Image** element (asset `kaixn-enso-mark.svg`) before the
  "kaixn" wordmark in the `nav-brand` link.
- New `nav-enso` class (24px tall); `nav-brand` made a centered flex row with 8px gap.
- Snapshot verified: the brush ensō renders cleanly beside the serif wordmark —
  fixing the earlier raw-`<img>`/`imgraw` rendering failure.

### 4. "Log in" routing confirmed ✅
Both entry points are plain URL links to the app:
- Nav "Log in" (`nav-link`) → `https://app.kaixn.com`
- Footer "Log in" (`footer-link`) → `https://app.kaixn.com`

### 5. Fonts + form scripts registered (not yet applied)
Two inline scripts were registered on the site but **could not be applied** — header/
footer custom code requires a paid Webflow Site plan (apply returned 404
`Custom code block not found`):
- `kaixnfonts` — loads real Fraunces + Inter via Google Fonts
- `kaixnwaitlist` — AJAX-POSTs the form with in-place confirmation

They remain registered, so once on a paid plan each applies in a single call.

### 6. SEO / Open Graph — already complete
Home page title, description, and OG tags were already set in a prior session; no
change needed.

## Open items / blockers (for whoever picks this up)

1. **Verify the live form submit.** Couldn't be tested from the work machine: it sits
   behind a **Dope Security** web gateway that blocks `app.kaixn.com` as uncategorized
   (returns *"Content blocked by your organization"*, header `dope-ep-block-page`).
   → Allowlist `app.kaixn.com` in Dope, or test from an unfiltered network. Also
   confirm prod actually has the `/api/waitlist` endpoint deployed (`deploy/deploy.sh`).
   Watch for Webflow's own form JS intercepting submit on the published site.
2. **Real Fraunces/Inter font files.** Add via **Site Settings → Fonts** (dashboard) —
   the style variables already reference these families with fallbacks, so they apply
   automatically once added. (Custom-code injection is paid-plan only.)
3. **Custom domain `kaixn.com` / `www`.** Requires a paid Webflow Site plan + DNS.
4. **Paid plan → unlocks** applying the two registered scripts and connecting the
   domain, both headlessly.

## Notes for working in the Webflow Designer via MCP
- Designer tools (elements/styles/assets/snapshots) require the **MCP companion app
  running inside the foregrounded Designer tab**; the `?app=<hash>` deep-link launches
  it. The socket drops/idles intermittently — keep the tab active and just retry.
- Data API tools (sites/pages/scripts/publish) work headless without the companion app.
