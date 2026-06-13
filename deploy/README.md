# Deploying kaixn to AWS

One CloudFormation stack: **ECS Fargate** (the web app) behind an **Application
Load Balancer**, backed by **RDS PostgreSQL 16** with `pgvector` + `ltree`. API
keys are stored in **Secrets Manager** and injected into the task — never baked
into the image.

```
Internet ──▶ ALB :80 ──▶ Fargate task :8000 (kaixn web) ──▶ RDS Postgres :5432
                                  │
                          Secrets Manager (ANTHROPIC / OPENAI / DB password)
```

## Prerequisites

- AWS CLI v2, configured: `aws configure`
- Docker running locally
- An IAM principal allowed to manage ECR, ECS, RDS, EC2/VPC, ELBv2, IAM, Secrets Manager

## Deploy

```bash
export DB_PASSWORD='pick-a-strong-alphanumeric-password'
export ANTHROPIC_API_KEY='sk-ant-...'   # optional — omit for offline mode
export OPENAI_API_KEY='sk-...'          # optional — only if using openai embedder
export KAIXN_EMBEDDER=openai            # optional — default 'fake'
export AWS_REGION=us-east-1             # optional — default us-east-1

./deploy/deploy.sh
```

The script builds + pushes the image to ECR, deploys the stack, and prints the
public URL. First run provisions RDS and takes ~10 minutes. Re-running rebuilds
the image and forces a new service deployment.

## What you get

- A public URL (`http://<alb-dns>`) serving the UI — paste a GitHub URL and run
  the full loop.
- Schema auto-applied on task start (`scripts/apply_migrations.py`), with the
  vector width matched to the chosen embedder.
- Logs in CloudWatch under `/ecs/<stack>`.

## Notes & cost

- Defaults are demo-sized: `db.t3.micro`, 1 Fargate task (0.5 vCPU / 1 GB). Roughly
  a few dollars/day; **tear down when done.**
- The stack runs Fargate tasks in public subnets (public IP) to reach ECR and to
  `git clone` user-submitted repos — no NAT gateway, keeping cost down. RDS is
  private and only reachable from the task security group.
- HTTP only (no TLS). For production, add an ACM cert + an HTTPS listener, and
  consider putting tasks in private subnets behind a NAT gateway.
- `embedder=fake` works with no external keys and is fine for demos; switch to
  `openai` for real semantic retrieval.

## Tear down

```bash
aws cloudformation delete-stack --stack-name kaixn --region "$AWS_REGION"
```

RDS is created with a `Snapshot` deletion policy, so a final snapshot is taken.
Delete the leftover snapshot and the ECR repo manually if you want zero cost.
