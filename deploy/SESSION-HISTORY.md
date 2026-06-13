# Deploy session history — kaixn → AWS

Chronological record of the DevOps work that took kaixn from "code in a repo" to a
live, HTTPS, custom-domain deployment on AWS. Written 2026-06-13.

> **TL;DR — final live state**
> | Host | Behavior |
> |---|---|
> | `app.kaixn.com` | serves the app (HTTPS) |
> | `www.kaixn.com` | 301 → `https://kaixn.webflow.io/` (marketing) |
> | `kaixn.com` | 301 → `https://kaixn.webflow.io/` (marketing) |
>
> Stack `kaixn` in account **643306905656** (`personal` profile), **us-east-1**.
> ECS Fargate + ALB + RDS Postgres (pgvector), secrets on a customer-managed KMS
> key, OpenAI embedder + Anthropic LLM enabled.

---

## Environment / account

- **AWS profile:** `personal` — IAM user `anusual`, account `643306905656` (NOT the
  Brevian dev/prod SSO profiles that also live in `~/.aws`).
- **Region:** `us-east-1` · **Stack:** `kaixn` · **ECR repo:** `kaixn`.
- **Route53 hosted zone:** `Z00813361E3D6X2S4N5SG` (`kaixn.com`, same account — makes
  ACM DNS-validation and alias records automatic).
- **Local secret files (gitignored, never committed):**
  - `deploy/.db_password.local` — RDS master password (must be reused on every redeploy).
  - `deploy/.secrets.local` — `ANTHROPIC_API_KEY` + `OPENAI_API_KEY`, copied from `.env`.

---

## Timeline

### 1. First deploy — prove the stack (offline mode)
- Reviewed the existing `deploy/` stack; it was complete and consistent
  (`/api/health` matches the ALB health check, entrypoint composes the DSN from
  parts and applies migrations) — **no code changes needed to deploy**.
- Generated an RDS password → `deploy/.db_password.local`.
- Ran `deploy.sh` with `KAIXN_EMBEDDER=fake`, no API keys.
- **Verified green:** stack `CREATE_COMPLETE`, ECS 1/1 task, ALB target `healthy`,
  `/api/health` → `200 {"ok":true}`, `/api/status` → `backend: postgres`.
- Result: `http://kaixn-Alb-UtUTEOf9g14l-316477744.us-east-1.elb.amazonaws.com`.

### 2. Custom domain `app.kaixn.com` + HTTPS
- Added to `cloudformation.yaml` (gated on optional `DomainName`/`HostedZoneId` params
  so the keyless/no-domain path still works):
  - **ACM certificate** for the domain, DNS-validated automatically via Route53.
  - **HTTPS:443 listener** using the cert; **HTTP:80 → HTTPS 301 redirect**.
  - **Route53 alias A-record** → ALB.
  - Opened **443** in the ALB security group.
  - `Url` output switches to `https://<domain>` when a domain is set.
- `deploy.sh` threads `DOMAIN_NAME` / `HOSTED_ZONE_ID` through.
- Result: `https://app.kaixn.com`.

### 3. `www` + apex + KMS + "real" mode
- **KMS:** added a customer-managed CMK (`alias/kaixn-secrets`, key rotation on) and
  pointed the `kaixn-secrets` Secrets Manager secret at it (was the default
  AWS-managed key). Granted the ECS **execution role `kms:Decrypt`** on the CMK.
- **Domains:** cert now carries SANs `app` + `www` + apex; added Route53 alias
  records for `www.kaixn.com` and `kaixn.com`. New optional params `WwwDomain`,
  `ApexDomain`.
- **Real LLM + embeddings:** switched to `KAIXN_EMBEDDER=openai` (1536-dim) and
  enabled Anthropic synthesis. Keys copied from `.env` → `deploy/.secrets.local`.
- **Self-healing migration:** `scripts/apply_migrations.py` now compares the live
  embedding column's dimension to the active embedder's; on a mismatch it recreates
  the schema **only when the schema is empty** (refuses if rows exist, to protect
  data). The 64→1536 switch ran automatically — logs:
  `embedding dim mismatch (db=64 -> 1536) and schema is empty — recreating public schema`.
- **Verified:** secret `KmsKeyId` → CUSTOMER-managed key; `/api/status` →
  `embedder: OpenAIEmbedder, embed_dim: 1536, llm_enabled: true`; cert SANs cover all
  three hosts; all three return `200`.

### 4. `kaixn.com` → Webflow redirect
- Added optional `ApexRedirectHost` param. When set, **ALB ListenerRules** on both
  :80 and :443 match `host-header: kaixn.com` and **301-redirect to
  `https://kaixn.webflow.io/`**. `kaixn.com` stays on the cert + an ALB alias so the
  TLS handshake completes before the redirect fires. app/www keep serving the app.
- **Build failure encountered + fixed:** the image build broke with a pip
  `ResolutionImpossible` — `pgvector` pulls `numpy` unpinned, and a freshly-published
  numpy had no cp312 wheel (the slim image has no compiler to build the sdist).
  Pinned **`numpy>=1.26,<2.5`** in the `postgres` extra of `pyproject.toml`.
- **API-key scare:** flagged a possibly-checked-in key. Scanned all git history
  (`git rev-list --all`) + the tree — **clean**; `.env`/`.env.save` were never
  committed. Confirmed false alarm, no rotation needed.

### 5. `www.kaixn.com` → Webflow redirect
- Extended the apex ListenerRules to also match `www.kaixn.com` (one rule per
  listener, `Values: [apex, www]` when `WwwDomain` is set) so `www` mirrors the bare
  domain to marketing.
- **Verified:** `kaixn.com` and `www.kaixn.com` both `301 → https://kaixn.webflow.io/`;
  `app.kaixn.com` still `200`.

---

## Files changed this session

- `deploy/cloudformation.yaml` — ACM cert (+SANs), HTTPS listener, HTTP→HTTPS redirect,
  Route53 alias records (app/www/apex), 443 SG ingress, customer-managed KMS key +
  alias + exec-role `kms:Decrypt`, apex/www → external redirect ListenerRules.
  New params: `DomainName`, `WwwDomain`, `ApexDomain`, `ApexRedirectHost`, `HostedZoneId`.
- `deploy/deploy.sh` — threads `DOMAIN_NAME`, `WWW_DOMAIN`, `APEX_DOMAIN`,
  `APEX_REDIRECT_HOST`, `HOSTED_ZONE_ID` into the CloudFormation parameter overrides.
- `scripts/apply_migrations.py` — self-healing embedding-dimension migration.
- `pyproject.toml` — pinned `numpy>=1.26,<2.5` in the `postgres` extra.
- `.gitignore` — added `deploy/.db_password.local`, `deploy/.secrets.local`.

---

## Gotchas (read before re-deploying)

1. **Corporate proxy blocks `*.kaixn.com` locally.** Brevian's endpoint-security
   agent SSL-intercepts by SNI/Host and returns a `403 "Content blocked … CATEGORY:
   Pornography"` page on the work laptop — this is **not** AWS. Verify the app from
   off-network (phone on cellular) or by hitting the ALB hostname directly with a
   `Host:` header:
   ```bash
   ALB=kaixn-Alb-UtUTEOf9g14l-316477744.us-east-1.elb.amazonaws.com
   curl -sk -H "Host: app.kaixn.com" https://$ALB/api/status
   ```
   To use the site on the corporate network, get `*.kaixn.com` recategorized/allowlisted
   in the Brevian/Netskope console.
2. **Reuse the same DB password** on every redeploy (`deploy/.db_password.local`) —
   it's the RDS master password; changing it mid-stream causes drift.
3. **Always pass the domain params** on redeploy, or the listeners/DNS/redirects get
   torn down. The other agents redeploying this repo must use the full command below.
4. **Embedder dimension** is baked into the schema. Switching embedders only succeeds
   automatically while the DB is empty (see the self-healing migration); with data
   present the migration refuses and you must migrate/drop manually.

---

## Runbook — full redeploy (preserves everything)

```bash
cd <repo root>
set -a; . deploy/.secrets.local; . deploy/.db_password.local; set +a
export AWS_PROFILE=personal AWS_REGION=us-east-1 KAIXN_EMBEDDER=openai
export DOMAIN_NAME=app.kaixn.com WWW_DOMAIN=www.kaixn.com APEX_DOMAIN=kaixn.com
export APEX_REDIRECT_HOST=kaixn.webflow.io
export HOSTED_ZONE_ID=Z00813361E3D6X2S4N5SG
./deploy/deploy.sh
```

`deploy.sh` rebuilds + pushes the image, runs `aws cloudformation deploy` (idempotent),
and force-rolls the ECS service to pick up the new image.

### Tear down
```bash
aws cloudformation delete-stack --stack-name kaixn --profile personal --region us-east-1
```
RDS has a `Snapshot` deletion policy — delete the leftover final snapshot, the ECR
repo, and the KMS key (schedule deletion) afterward for zero cost.
