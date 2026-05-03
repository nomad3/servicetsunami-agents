# infra/

Infrastructure-as-Code for AWS deployments + the Dockerfiles for the API and Web services.

The primary local runtime is **docker-compose** (root `docker-compose.yml`). The primary production-path runtime is **Rancher Desktop K8s + Helm** (see [`../helm/`](../helm/) and [`../docs/KUBERNETES_DEPLOYMENT.md`](../docs/KUBERNETES_DEPLOYMENT.md)). The Terraform here is the AWS EKS path which is currently not the deploy target — kept for reference and for tenants that need an AWS deployment.

## Layout

```
infra/
├── docker/
│   ├── api.Dockerfile             # FastAPI backend image (legacy, root Dockerfiles take precedence)
│   ├── web.Dockerfile             # React SPA dev image
│   └── web.production.Dockerfile  # React SPA production (nginx)
└── terraform/
    ├── main.tf                    # AWS provider, EKS cluster, Aurora PostgreSQL, VPC
    ├── variables.tf               # project, environment, region, cluster_version, ...
    └── outputs.tf                 # cluster endpoint, kubeconfig, RDS endpoint
```

## Terraform — AWS path

```bash
cd infra/terraform
terraform init
terraform plan -var="environment=staging"
terraform apply
```

Provisions:

- **VPC** with public + private subnets across 3 AZs.
- **EKS** control plane (configurable `cluster_version`).
- **Aurora PostgreSQL** with pgvector (matching local Postgres 13 + pgvector contract).
- IAM roles, security groups, KMS keys.

Outputs `kubeconfig` so you can immediately Helm-install on top:

```bash
terraform output -raw kubeconfig > ~/.kube/agentprovision-aws.yaml
KUBECONFIG=~/.kube/agentprovision-aws.yaml helm upgrade --install agentprovision-api \
  ../../helm/charts/microservice \
  --values ../../helm/values/agentprovision-api.yaml
```

## Drift discipline

When making manual changes, **mirror them into Helm + Git + Terraform** to prevent drift. This is a hard rule from [`../CLAUDE.md`](../CLAUDE.md).

## See also

- [`../docs/KUBERNETES_DEPLOYMENT.md`](../docs/KUBERNETES_DEPLOYMENT.md) — full K8s deployment runbook (covers both Rancher Desktop local and EKS production paths).
- [`../helm/README.md`](../helm/README.md) — Helm chart reference, values structure, debugging.
- [`../kubernetes/README.md`](../kubernetes/README.md) — cluster-level manifests applied outside of Helm.
