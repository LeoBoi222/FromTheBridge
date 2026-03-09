# FromTheBridge Repo Structure
# Full Path: ~/Home/Projects/FromTheBridge/
# Git: Monorepo for Claude Code + layer isolation

## Root Files (Essential)

~/Home/Projects/FromTheBridge/
├── README.md # Mission, quickstart, architecture summary
├── CLAUDE.md # Claude Code agent brain (read first every session)
├── Makefile # make dev test-[layer] lint deploy
├── docker-compose.yml # Local stack: ClickHouse/Postgres/MinIO/Kafka
├── .gitignore # Docker images, pycache, .env
├── requirements.txt # Root: pydantic, httpx, pytest
├── pyproject.toml # ruff/black/mypy config
├── .env.example # API keys template

text

## docs/ (Perplexity Space Sync)

docs/
├── architecture.yaml # Canonical v1.2 (Space master)
├── layers.md # Layer responsibilities/done criteria
├── roadmap.md # Phase 1-3 milestones
├── sources.yaml # L1 vendor catalog (Space → here)
└── decisions/ # 2026-03-04-claude-review.md

text
**Sync**: Space exports → `cp ~/Downloads/* docs/` → `git commit "docs: Space sync"`

## shared/ (Cross‑Layer)

shared/
├── schemas/ # Canonical DDL/JSON Schema
│ ├── silver_instrument.sql
│ ├── silver_prices.sql
│ └── pydantic_models.py # API types
├── utils/
│ ├── lineage.py # Hashing
│ └── validation.py # Great Expectations helpers
└── types/
└── base.py # PydanticBaseModel

text

## layers/ (Claude Works Here - ONE Layer/Session)

layers/
├── l1_sources/ # Vendor clients
│ ├── README.md # Step-by-step
│ ├── sources.yaml # Vendors + failover
│ ├── polygon_client.py
│ ├── tests/test_polygon.py
│ └── requirements.txt
├── l2_ingestion/ # Dagster pipelines
│ ├── README.md
│ ├── dagster_project.yaml
│ └── pipelines/
├── l3_lakehouse/ # Schemas + Docker
│ ├── README.md
│ ├── schemas/silver_ddl.sql # v1.2 exact
│ ├── docker/
│ └── tests/
├── l4_semantic/ # dbt marts
│ ├── dbt_project.yml
│ └── models/
├── l5_serving/ # FastAPI + Next.js
│ ├── api/main.py
│ └── ui/
├── l6_analytics/ # Factors notebooks
│ └── notebooks/
└── l7_governance/ # OpenMetadata config

text

## infra/ (Proxmox Setup)

infra/
└── ansible/ # VM provisioning
└── proxmox.yml

text

## tests/ (E2E)

tests/
└── integration/ # Layer handoffs
└── test_l1_to_l2.py

text

## Workflow (Claude + Manual)

    Space Layers Thread → L1 files

    cp to ~/Home/Projects/FromTheBridge/layers/l1_sources/

    cd ~/Home/Projects/FromTheBridge/

    make dev # Docker stack up

    make test-l1 # pytest pass

    git add/commit # "feat(l1): polygon client"

    Space: "L1 done ✅ → L2 spec"

text

## Git Commands
```bash
cd ~/Home/Projects/FromTheBridge/
git add .
git commit -m "feat(l1): [description]"
git push origin main

make Commands (TBD Content)

text
make dev           # docker-compose up
make test-l1       # pytest layers/l1_sources
make lint          # ruff/black/mypy
make docs-sync     # Space → docs/ (manual for now)

