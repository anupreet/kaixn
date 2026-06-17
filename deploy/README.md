# Deploying kaixn to AWS

One CloudFormation stack: **ECS Fargate** (the web app) behind an **Application
Load Balancer** with HTTPS, backed by **RDS PostgreSQL 16** (`pgvector` + `ltree`).
API keys live in **Secrets Manager**, encrypted with a **customer-managed KMS key**,
and are injected into the task — never baked into the image. Custom domains, TLS, and
host-based redirects are all driven from the template.

```
Internet ─▶ ALB :443 (ACM cert) ─▶ Fargate task :8000 (kaixn web) ─▶ RDS Postgres :5432
            :80 ─▶ 301 :443                    │
            host=kaixn.com/www ─▶ 301 webflow   └─ Secrets Manager (KMS CMK): ANTHROPIC / OPENAI / DB_PASSWORD
```

## Prerequisites

- AWS CLI v2, Docker running locally.
- An AWS profile with permissions for ECR, ECS, RDS, EC2/VPC, ELBv2, IAM, KMS,
  Secrets Manager, ACM, and Route53. This project uses the **`personal`** profile
  (account `643306905656`).
- For a custom domain: a **Route53 hosted zone** for it in the same account.

## Quick start (no domain, offline mode)

The minimum to get a running URL — in-memory-free Postgres stack, no keys, ALB DNS
over plain HTTP:

```bash
export AWS_PROFILE=personal AWS_REGION=us-east-1
export DB_PASSWORD='pick-a-strong-alphanumeric-password'
export KAIXN_EMBEDDER=fake          # deterministic, no external keys
./deploy/deploy.sh
```

The script builds + pushes the image to ECR, deploys the stack, and prints the URL.
First run provisions RDS and takes ~10 min.

## Production deploy (custom domain, HTTPS, real LLM) — what's live

This is the actual configuration running today (`app.kaixn.com`, marketing redirects,
KMS-encrypted keys, OpenAI embedder).

**1. Put secrets in gitignored files** (so they never touch the shell history or git):

```bash
# RDS master password — must be REUSED on every redeploy
printf 'DB_PASSWORD=%s\n' 'your-strong-pw' > deploy/.db_password.local

# LLM keys (these are also in .env; copy just these two lines)
grep -E '^(ANTHROPIC_API_KEY|OPENAI_API_KEY)=' .env > deploy/.secrets.local
```

**2. Deploy:**

```bash
set -a; . deploy/.secrets.local; . deploy/.db_password.local; set +a
export AWS_PROFILE=personal AWS_REGION=us-east-1 KAIXN_EMBEDDER=openai
export DOMAIN_NAME=app.kaixn.com WWW_DOMAIN=www.kaixn.com APEX_DOMAIN=kaixn.com
export APEX_REDIRECT_HOST=kaixn.webflow.io
export HOSTED_ZONE_ID=Z00813361E3D6X2S4N5SG
./deploy/deploy.sh
```

That single command (re)deploys everything: image, ACM cert (DNS-validated), HTTPS
listener + HTTP→HTTPS redirect, Route53 alias records, the KMS-encrypted secret, and
the host-based redirects. Re-running rebuilds the image and force-rolls the service.

## Configuration reference

Set as env vars before `deploy.sh`; the script maps them to CloudFormation parameters.

| Env var | CFN param | Default | Purpose |
|---|---|---|---|
| `DB_PASSWORD` | `DBPassword` | — (required) | RDS master password. **Reuse the same value every deploy.** |
| `KAIXN_EMBEDDER` | `KaixnEmbedder` | `openai` | `fake` (64-dim, no keys) · `openai` (1536-dim) · `ollama`. |
| `ANTHROPIC_API_KEY` | `AnthropicApiKey` | "" | Enables LLM synthesis/adjudication. Blank = offline. |
| `OPENAI_API_KEY` | `OpenAiApiKey` | "" | Required when `KAIXN_EMBEDDER=openai`. |
| `DOMAIN_NAME` | `DomainName` | "" | Primary host that serves the app (e.g. `app.kaixn.com`). Blank = ALB DNS, HTTP only. |
| `WWW_DOMAIN` | `WwwDomain` | "" | `www` host (added to the cert; redirected to `APEX_REDIRECT_HOST` if set). |
| `APEX_DOMAIN` | `ApexDomain` | "" | Bare domain (e.g. `kaixn.com`). |
| `APEX_REDIRECT_HOST` | `ApexRedirectHost` | "" | If set, apex + www are 301'd here (e.g. `kaixn.webflow.io`) instead of serving the app. |
| `HOSTED_ZONE_ID` | `HostedZoneId` | "" | Route53 zone for the domains (required when `DOMAIN_NAME` is set). |
| `AWS_REGION` | — | `us-east-1` | Region. |
| `STACK` / `ECR_REPO` | — | `kaixn` | Stack and ECR repo names. |

## What you get

- HTTPS UI at your domain; HTTP and the marketing hosts redirect appropriately.
- Schema auto-applied on task start (`scripts/apply_migrations.py`), with the vector
  width matched to the chosen embedder. The migration **self-heals** an embedder
  change by recreating the schema **only when it's empty** (it refuses to drop data).
- Secrets encrypted with a customer-managed KMS CMK (`alias/<stack>-secrets`, rotation
  enabled).
- Logs in CloudWatch under `/ecs/<stack>`.

## Verifying a deploy

```bash
ALB=$(aws elbv2 describe-load-balancers --profile personal --region us-east-1 \
  --query "LoadBalancers[?contains(LoadBalancerName,'kaixn')].DNSName | [0]" --output text)

# App status (Host header bypasses any local DNS/SNI filtering)
curl -sk -H "Host: app.kaixn.com" "https://$ALB/api/status"
# → {"backend":"postgres","embedder":"OpenAIEmbedder","embed_dim":1536,"llm_enabled":true,...}

# Redirects
curl -sk -H "Host: kaixn.com"     -o /dev/null -w "%{http_code} %{redirect_url}\n" "https://$ALB/"
curl -sk -H "Host: www.kaixn.com" -o /dev/null -w "%{http_code} %{redirect_url}\n" "https://$ALB/"
```

> **Note:** a corporate SSL-inspecting proxy may block `*.kaixn.com` from a work
> machine (returns a `403` block page that is *not* from AWS). Verify off-network or
> via the ALB-hostname + `Host:` trick above.

## Notes & cost

- Defaults are demo-sized: `db.t3.micro`, 1 Fargate task (0.5 vCPU / 1 GB) — a few
  dollars/day. **Tear down when done.**
- Fargate tasks run in public subnets (public IP) to reach ECR and to `git clone`
  user-submitted repos — no NAT gateway, keeping cost down. RDS is private.

## Tear down

```bash
aws cloudformation delete-stack --stack-name kaixn --profile personal --region us-east-1
```

RDS has a `Snapshot` deletion policy (final snapshot taken). For zero cost afterward,
delete the leftover snapshot, the ECR repo, and schedule deletion of the KMS key.
