# Architecture / Design Playbook — encode/starlette

> Reviewer-grade map of the repo's *de facto* design. No team-authored standards
> exist; every claim below is inferred from the code and tagged with consistency
> and evidence so a reviewer can trust it without re-deriving it.

## 1. Architecture narrative

Starlette is a **lightweight async ASGI toolkit** — the lower-level web layer
that frameworks like FastAPI build on. It is not an application; it is a
*library of composable ASGI primitives*. Its defining design choices:

- **The ASGI callable is the universal seam.** Everything — applications,
  routes, middleware, responses, mounts — is an `ASGIApp`
  (`(scope, receive, send) -> None`). There are no ABCs; contracts are
  duck-typed callables plus a couple of `typing.Protocol`s. This is what makes
  the toolkit endlessly composable and dependency-light.
- **Layered toward that seam.** `types.py` sits at the base as the leaf
  vocabulary; `applications.py` is the composition root that wires
  routing → middleware → request/response on top of it. Dependencies point
  *downward* to ASGI primitives, never upward.
- **One concern per module.** requests / responses / routing / websockets /
  config / staticfiles / templating each get their own cohesive module — no
  grab-bag utility dumping grounds (internal helpers are quarantined in
  `_utils.py` / `_exception_handler.py`).
- **Constructor injection, no globals.** `Starlette(routes=, middleware=,
  exception_handlers=, lifespan=)` — the whole app graph is assembled from
  arguments and the middleware stack is built at startup.
- **Async-first, never block the loop.** Built natively on anyio; every
  potentially blocking call (file I/O, `os.stat`, sync handlers, WSGI apps,
  upload r/w) is offloaded to the anyio threadpool. This is a load-bearing
  invariant, not a nicety.
- **Strict at the edge, unopinionated inside.** External input is validated and
  normalized at the ASGI/HTTP boundary (host allowlists, signed cookies, path
  traversal defense, form size limits → 4xx); but response *shape* and error
  *body format* are deliberately left bare (plain-text errors, no envelope, no
  RFC-7807, stdlib `json` only), pushing opinionation downstream.

The throughline: **maximize composability and minimize opinion** — be a sharp,
safe set of primitives, and let frameworks layered on top decide policy.

## 2. Axes by group

### architecture-layering

| axis | value | tier | consistency | evidence |
|---|---|---|---|---|
| module-responsibility | single — one cohesive concern per module | governed | high | `starlette/requests.py`, `responses.py`, `routing.py`, `config.py` |
| layering-direction | layered toward the ASGI seam (`types.py` base, `applications.py` composition root) | governed | high | `applications.py`, `types.py`, `routing.py`, `middleware/errors.py` |
| seam-pattern | duck-typed ASGI callables + `typing.Protocol`; no ABCs | advisory | high | `types.py`, `middleware/__init__.py`, `_utils.py` |
| dependency-injection | constructor injection — routes/middleware/handlers/lifespan into `__init__` | advisory | high | `applications.py`, `middleware/__init__.py`, `routing.py` |
| impl-pairing | single — concrete impls only; no fake+real seam pairs | advisory | medium | `staticfiles.py`, `templating.py`, `testclient.py` |
| package-layout | flat — `starlette/` at repo root (no `src/`) | advisory | high | `__init__.py`, `middleware/`, `pyproject.toml` |
| public-surface | leading-underscore privacy; `__all__` only in `status.py` | advisory | high | `_utils.py`, `_exception_handler.py`, `status.py` |
| config-management | env-vars via `Config` (+ optional `.env`/cast); module constants for fixed tables | advisory | high | `config.py`, `status.py` |

### error-correctness

| axis | value | tier | consistency | evidence |
|---|---|---|---|---|
| error-signaling | raise typed exceptions (HTTP/WebSocket at boundary; RuntimeError/ValueError/domain internally) | governed | high | `exceptions.py`, `requests.py`, `responses.py`, `formparsers.py` |
| error-propagation | bubble to central boundary that catches/converts/re-raises; `raise X from e` only intermittent | governed | medium | `middleware/errors.py`, `middleware/exceptions.py`, `_exception_handler.py` |
| input-validation | at-boundary for untrusted input → 4xx; `assert` for programmer-error invariants | governed | high | `convertors.py`, `formparsers.py`, `requests.py`, `datastructures.py` |
| none-handling | explicit-optional `X \| None` + `is None` guards / `.get(k, default)` | governed | high | `exceptions.py`, `requests.py`, `datastructures.py`, `responses.py` |
| state-mutation | immutable-by-default multidicts/headers; explicit `Mutable*` opt-in; `scope` mutated in place by design | governed | high | `datastructures.py`, `middleware/exceptions.py`, `requests.py` |
| idempotency | memoized/guarded (`_body`/`_form` cache, "Stream consumed", post-startup mutation guard) | advisory | medium | `requests.py`, `applications.py`, `config.py` |
| resource-lifecycle | context-managed (`async with anyio.open_file`, task groups, `close()` for temp upload files) | governed | high | `responses.py`, `requests.py`, `datastructures.py`, `concurrency.py` |

### concurrency-reliability

| axis | value | tier | consistency | evidence |
|---|---|---|---|---|
| concurrency-model | async-first ASGI; anyio task groups; sync bridged to threadpool | governed | high | `concurrency.py`, `routing.py`, `_utils.py`, `middleware/base.py` |
| blocking-io | non-blocking — all blocking I/O offloaded via `anyio.to_thread`/`run_in_threadpool` | governed | high | `staticfiles.py`, `responses.py`, `datastructures.py`, `concurrency.py` |
| timeouts | none in-framework; delegated to the ASGI server (TestClient rejects a timeout arg) | advisory | high | `testclient.py`, `status.py`, `middleware/sessions.py` |
| graceful-degradation | auto-installed ServerError/Exception middleware map errors to responses; optional deps fail loudly only on use | governed | high | `middleware/errors.py`, `middleware/exceptions.py`, `templating.py` |
| caching | HTTP conditional caching (ETag/Last-Modified/304) + in-request body cache; no app-data memoization | advisory | high | `responses.py`, `staticfiles.py`, `middleware/base.py` |

### data-persistence

| axis | value | tier | consistency | evidence |
|---|---|---|---|---|
| serialization | stdlib `json` at the wire (strict/compact); `@dataclass` internally; no pydantic by design | advisory | high | `responses.py`, `requests.py`, `formparsers.py` |

### api-interface-design

| axis | value | tier | consistency | evidence |
|---|---|---|---|---|
| response-shape | bare/unopinionated — typed Response subclasses emit raw content, no envelope | advisory | high | `responses.py`, `responses.py:181`, `schemas.py:17` |
| error-response | plain-text/ad-hoc — `detail` as PlainText; default 500 literal; debug traceback page; no RFC-7807 | governed | high | `middleware/exceptions.py:65`, `middleware/errors.py:258`, `exceptions.py:7` |
| status-codes | semantic — full RFC-9110 constant registry; body-less 204/304 + 307-default redirects special-cased | advisory | high | `status.py:1`, `exceptions.py:7`, `middleware/exceptions.py:67`, `responses.py:204` |
| backward-compat | additive w/ deprecation cycle — `StarletteDeprecationWarning` + `__getattr__` shims; SemVer | governed | high | `exceptions.py:36`, `status.py:182`, `concurrency.py:18`, `routing.py:587` |

### security-boundaries

| axis | value | tier | consistency | evidence |
|---|---|---|---|---|
| trust-boundary | strict at ASGI/HTTP edge: hosts, origins, cookie sigs, static paths, form parts validated/normalized | governed | high | `middleware/trustedhost.py`, `staticfiles.py`, `middleware/sessions.py`, `middleware/cors.py` |
| authz-boundary | scope-based via central `requires()`/`has_required_scope`; opt-in per endpoint, not a forced gate | governed | medium | `authentication.py`, `middleware/authentication.py` |
| authn-pattern | pluggable `AuthenticationBackend` populating `scope['user']/['auth']`; signed-cookie sessions OOTB; no built-in JWT | governed | high | `middleware/authentication.py`, `authentication.py`, `middleware/sessions.py` |
| injection-safety | safe by construction: no SQL/shell; path traversal blocked via normpath/realpath/commonpath; Jinja2 autoescape | governed | high | `staticfiles.py`, `templating.py`, `formparsers.py` |
| secrets-handling | env/`.env` `Config` + `Secret` wrapper masking repr/tracebacks; secrets as ctor args, never literals | governed | high | `config.py`, `datastructures.py`, `middleware/sessions.py` |

> **N/A axes** (dimensions that don't apply to an inbound-only ASGI toolkit):
> `offline-fallback`, `retry-backoff`, `data-access`, `transaction-boundaries`,
> `schema-sot`, `versioning`, `pagination`. Starlette makes essentially no
> outbound calls and owns no datastore, so the persistence, resilience, and
> API-versioning dimensions have no surface here.

## 3. Patterns worth noting

1. **The callable-as-seam composition model.** A single type alias (`ASGIApp`)
   is simultaneously the app, the route, the middleware, and the mount. The
   entire framework is "functions that wrap functions." This is the source of
   its composability and is *stronger* than the generic `seam-pattern` axis
   captures. **Candidate new design-tier axis: `extension-seam-uniformity`** —
   does a system extend through one uniform contract vs. many bespoke plugin
   points? (meta-discovery)

2. **Auto-installed safety boundary.** `Starlette` always wraps the user stack
   with `ServerErrorMiddleware` (outermost) and `ExceptionMiddleware`
   (innermost) so *no* unhandled exception escapes as a raw ASGI crash — it
   becomes a clean 500 while still re-raising for server logs. The framework
   guarantees a property the user can't accidentally remove.

3. **Immutability with explicit mutable opt-in.** `Headers`/`QueryParams`/
   `FormData` are read-only by default; `MutableHeaders`/`MultiDict` are the
   deliberate escape hatch; the ASGI `scope` dict is the *one* sanctioned
   in-place mutation channel. Mutation is always a conscious, named choice.

4. **Deprecate-then-remove via a visible warning class.** `StarletteDeprecation
   Warning` subclasses `UserWarning` (shown by default, unlike the silenced
   stdlib `DeprecationWarning`), paired with module-level `__getattr__` shims
   that keep renamed symbols importable. A disciplined, *loud* evolution policy
   for a library with many downstream dependents.

5. **Lazy/guarded single-consumption.** Request body and form are parsed once
   and memoized (`_body`/`_form`); re-reading a stream raises
   `"Stream consumed"`. Repeatable reads are *engineered* on top of a
   fundamentally one-shot resource rather than assumed. The generic
   `idempotency` axis covers part of this, but the **"single-consumption
   resource guard"** flavor (one-shot streams made re-read-safe) may warrant its
   own design-tier axis. (meta-discovery)

6. **Opinion deferral as a stance.** Bare response shapes, plain-text errors,
   stdlib-only `json`, no pydantic, no timeouts, opt-in authz — recurring
   *deliberate non-decisions* that push policy to downstream frameworks.
   **Candidate new design-tier axis: `opinionation-level`** — does the layer
   enforce policy (envelopes, error schemas, validation libs) or deliberately
   defer it? (meta-discovery)

---

### Summary

1. Starlette is a lightweight async **ASGI toolkit** — composable web primitives
   meant to be built *on top of*, not an application.
2. Its universal seam is the duck-typed `ASGIApp` callable (no ABCs); everything
   is a function wrapping a function, layered downward toward `types.py`.
3. It is async-first on anyio with a load-bearing **never-block-the-loop**
   invariant: every blocking call is offloaded to the threadpool.
4. It is **strict at the edge** (host/origin/cookie/path/form validation,
   `Secret` masking) but **deliberately unopinionated inside** (bare responses,
   plain-text errors, stdlib json, opt-in authz).
5. Evolution is disciplined: SemVer + a loud `StarletteDeprecationWarning` +
   `__getattr__` shims for a deprecate-then-remove cycle.
6. **Candidate new design-tier axes (meta-discovery):**
   `extension-seam-uniformity` (one uniform extension contract vs. many),
   `opinionation-level` (enforce policy vs. defer it downstream), and a
   single-consumption-resource-guard flavor of `idempotency`.
