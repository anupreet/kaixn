#!/usr/bin/env bash
# One-shot AWS deploy: build the image, push to ECR, deploy the CloudFormation
# stack (ECS Fargate + ALB + RDS), and print the public URL.
#
# Prereqs: AWS CLI v2 configured (`aws configure`), Docker running, and an
# account with permissions for ECR/ECS/RDS/EC2/ELB/IAM/SecretsManager.
#
# Usage:
#   export DB_PASSWORD='choose-a-strong-pw'
#   export ANTHROPIC_API_KEY='sk-ant-...'      # optional (offline mode if unset)
#   export OPENAI_API_KEY='sk-...'             # optional (only for KAIXN_EMBEDDER=openai)
#   export KAIXN_EMBEDDER=fake                 # optional; default: openai
#   ./deploy/deploy.sh
#
# Env knobs: AWS_REGION (default us-east-1), STACK (default kaixn), ECR_REPO (default kaixn).
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK:-kaixn}"
ECR_REPO="${ECR_REPO:-kaixn}"
TAG="${TAG:-latest}"
EMBEDDER="${KAIXN_EMBEDDER:-openai}"
HERE="$(cd "$(dirname "$0")" && pwd)"

: "${DB_PASSWORD:?set DB_PASSWORD (>=8 chars, alphanumeric recommended)}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${ECR_REPO}:${TAG}"

echo "▸ ensuring ECR repo ${ECR_REPO}"
aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" >/dev/null

echo "▸ building & pushing ${IMAGE}"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"
docker build --platform linux/amd64 -t "$IMAGE" "$HERE/.."
docker push "$IMAGE"

echo "▸ deploying CloudFormation stack ${STACK} (this provisions RDS — ~10 min on first run)"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file "$HERE/cloudformation.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      ContainerImage="$IMAGE" \
      KaixnEmbedder="$EMBEDDER" \
      AnthropicApiKey="${ANTHROPIC_API_KEY:-}" \
      OpenAiApiKey="${OPENAI_API_KEY:-}" \
      GithubClientId="${KAIXN_GITHUB_CLIENT_ID:-}" \
      GithubClientSecret="${KAIXN_GITHUB_CLIENT_SECRET:-}" \
      SessionSecret="${KAIXN_SESSION_SECRET:-}" \
      DBPassword="$DB_PASSWORD" \
      DomainName="${DOMAIN_NAME:-}" \
      WwwDomain="${WWW_DOMAIN:-}" \
      ApexDomain="${APEX_DOMAIN:-}" \
      ApexRedirectHost="${APEX_REDIRECT_HOST:-}" \
      HostedZoneId="${HOSTED_ZONE_ID:-}"

URL="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='Url'].OutputValue" --output text)"
echo
echo "✅ deployed. kaixn UI: ${URL}"
echo "   (first request may take ~30s while the task starts and the schema is applied)"

# Redeploys: if the stack already exists, `aws cloudformation deploy` is a no-op
# for unchanged infra, but a new image with the same tag won't roll the service.
# Force a fresh deployment to pull the latest image:
if aws ecs list-services --cluster "${STACK}-cluster" --region "$REGION" >/dev/null 2>&1; then
  SVC="$(aws ecs list-services --cluster "${STACK}-cluster" --region "$REGION" \
          --query 'serviceArns[0]' --output text 2>/dev/null || true)"
  if [[ -n "$SVC" && "$SVC" != "None" ]]; then
    echo "▸ rolling the service to pick up the new image"
    aws ecs update-service --cluster "${STACK}-cluster" --service "$SVC" \
      --force-new-deployment --region "$REGION" >/dev/null
  fi
fi
