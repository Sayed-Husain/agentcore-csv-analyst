# Teardown checklist

Tracks AWS resources `agentcore deploy` provisions, plus the cleanup commands
they don't auto-handle. The rule: **anything that costs money while idle goes
in here the moment we make it.** Walk this list end-to-end before abandoning
the project.

This project deploys via the local `agentcore` CLI only — there is no CI/CD
infrastructure to clean up. (Phase 2 / CodeBuild was scoped out to a separate
repo.)

---

## What `agentcore deploy` creates

| Resource | Pattern | Cost characteristic |
| --- | --- | --- |
| AgentCore Runtime | `csv_analyst-<random suffix>` | per-invoke + tiny idle |
| ECR repository | `bedrock-agentcore-csv_analyst` | pennies/month storage |
| ECR image | tag like `20260503-174232-607` | counted against ECR storage |
| CodeBuild project (toolkit's builder) | `bedrock-agentcore-csv_analyst-builder` | free idle, per-minute when building |
| S3 source bucket | `bedrock-agentcore-codebuild-sources-<account>-<region>` | pennies/month |
| IAM role (runtime) | `AmazonBedrockAgentCoreSDKRuntime-<region>-<hash>` | free |
| IAM role (toolkit codebuild) | `AmazonBedrockAgentCoreSDKCodeBuild-<region>-<hash>` | free |
| CloudWatch log group (runtime) | `/aws/bedrock-agentcore/runtimes/csv_analyst-<id>-DEFAULT` | pennies (retention) |
| CloudWatch log group (codebuild) | `/aws/codebuild/bedrock-agentcore-csv_analyst-builder` | pennies (retention) |

Total idle baseline: single-digit cents/month.

---

## Step 1 — Toolkit teardown

`agentcore destroy` covers the runtime, ECR repo (and images), and the
toolkit's CodeBuild project.

```powershell
agentcore destroy
```

It does **not** clean up: IAM roles, the S3 source bucket, or any CloudWatch log
groups. Continue with Step 2.

---

## Step 2 — Manual cleanup

Each command is independent so failures don't cascade. Resource names are
looked up rather than hardcoded so this stays correct even if `agentcore deploy`
is re-run later with new random suffixes.

```powershell
$AWS_ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$REGION = "us-east-1"

# 1. Runtime IAM role + inline policy
$RUNTIME_ROLE = (aws iam list-roles `
  --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime-')].RoleName | [0]" `
  --output text)
if ($RUNTIME_ROLE -and $RUNTIME_ROLE -ne "None") {
  $POLICY_NAME = (aws iam list-role-policies --role-name $RUNTIME_ROLE --query "PolicyNames[0]" --output text)
  if ($POLICY_NAME -and $POLICY_NAME -ne "None") {
    aws iam delete-role-policy --role-name $RUNTIME_ROLE --policy-name $POLICY_NAME
  }
  aws iam delete-role --role-name $RUNTIME_ROLE
}

# 2. Toolkit's CodeBuild IAM role + inline policy
$CB_ROLE = (aws iam list-roles `
  --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKCodeBuild-')].RoleName | [0]" `
  --output text)
if ($CB_ROLE -and $CB_ROLE -ne "None") {
  $POLICY_NAME = (aws iam list-role-policies --role-name $CB_ROLE --query "PolicyNames[0]" --output text)
  if ($POLICY_NAME -and $POLICY_NAME -ne "None") {
    aws iam delete-role-policy --role-name $CB_ROLE --policy-name $POLICY_NAME
  }
  aws iam delete-role --role-name $CB_ROLE
}

# 3. CodeBuild source bucket — empty (handles versions) then delete
$SOURCE_BUCKET = "bedrock-agentcore-codebuild-sources-$AWS_ACCOUNT_ID-$REGION"
aws s3 rb "s3://$SOURCE_BUCKET" --force

# 4. Runtime CloudWatch log group (look up by prefix)
$RUNTIME_LG = (aws logs describe-log-groups `
  --region $REGION `
  --log-group-name-prefix /aws/bedrock-agentcore/runtimes/csv_analyst `
  --query "logGroups[0].logGroupName" --output text)
if ($RUNTIME_LG -and $RUNTIME_LG -ne "None") {
  aws logs delete-log-group --log-group-name $RUNTIME_LG --region $REGION
}

# 5. Toolkit CodeBuild log group
aws logs delete-log-group `
  --log-group-name /aws/codebuild/bedrock-agentcore-csv_analyst-builder `
  --region $REGION
```

---

## Step 3 — Verify nothing's left

Each query should return empty / `None` / no rows.

```powershell
aws bedrock-agentcore-control list-agent-runtimes --region us-east-1
aws ecr describe-repositories --region us-east-1 `
  --query "repositories[?contains(repositoryName, 'csv_analyst')]"
aws codebuild list-projects --region us-east-1 `
  --query "projects[?contains(@, 'csv_analyst')]"
aws iam list-roles --query "Roles[?contains(RoleName, 'csv_analyst') || contains(RoleName, 'AgentCoreSDK')].RoleName"
aws s3 ls | Select-String agentcore
aws bedrock-agentcore-control list-code-interpreter-sessions --region us-east-1
```

---

## Self-cleaning (no action needed, listed for awareness)

- **Code Interpreter sessions** — the `with code_session(...)` context manager
  calls `StopCodeInterpreterSession` on exit. Orphaned sessions auto-expire
  after `sessionTimeoutSeconds` (default 900s).

## Pay-per-use (zero cost when idle)

Stop calling these and they stop charging. No teardown action.

- Bedrock model invocations
- AgentCore Code Interpreter invocations
- AgentCore Runtime invocations

---

## End-of-project checklist

- [ ] `agentcore destroy` ran without errors
- [ ] All Step 2 commands executed
- [ ] All Step 3 verification queries return empty
- [ ] Delete local venv: `Remove-Item -Recurse -Force .venv`
