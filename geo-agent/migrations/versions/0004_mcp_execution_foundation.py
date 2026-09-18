"""Add the durable execution and bounded-read foundation for MCP."""

from collections.abc import Sequence
from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0004_mcp_execution_foundation"
down_revision: str | None = "0003_brand_definitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _summary(payload: dict) -> dict:
    inputs = payload.get("inputs")
    brief = payload.get("brief") or (inputs or {}).get("brief")
    return {
        "run_id": payload["run_id"],
        "revision": payload["revision"],
        "state": payload["state"],
        "brief_url": (brief or {}).get("url"),
        "profile_ids": [
            profile["profile_id"]
            for profile in (inputs or {}).get("profiles", [])
        ],
        "approval_status": (
            "approved"
            if payload.get("approval") is not None
            else "pending"
            if inputs is not None
            else "not-ready"
        ),
        "has_measurement": payload.get("measurement") is not None,
        "created_at": payload["created_at"],
        "updated_at": payload["updated_at"],
    }


def _interrupt_legacy_jobs(connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = connection.execute(sa.text(
        "SELECT job_id, run_id, payload FROM workflow_jobs "
        "WHERE state IN ('queued', 'leased') AND policy_id IS NULL"
    )).mappings().all()
    updated_runs: set[str] = set()
    for row in rows:
        job = json.loads(row["payload"])
        job.update({
            "state": "failed",
            "lease_holder": None,
            "lease_token": None,
            "lease_expires_at": None,
            "error_code": "migration-review-required",
            "completed_at": now,
            "updated_at": now,
        })
        connection.execute(sa.text(
            "UPDATE workflow_jobs SET state = 'failed', lease_holder = NULL, "
            "lease_token = NULL, lease_expires_at = NULL, "
            "completed_at = :now, error_code = 'migration-review-required', "
            "payload = :payload, updated_at = :now WHERE job_id = :job_id"
        ), {
            "job_id": row["job_id"],
            "payload": json.dumps(job, separators=(",", ":"), sort_keys=True),
            "now": now,
        })
        if row["run_id"] in updated_runs:
            continue
        run_row = connection.execute(
            sa.text("SELECT payload FROM measurement_runs WHERE run_id = :run_id"),
            {"run_id": row["run_id"]},
        ).mappings().one()
        run = json.loads(run_row["payload"])
        events = list(run.get("events", []))
        event = {
            "sequence": len(events) + 1,
            "event_type": "job-interrupted",
            "occurred_at": now,
        }
        events.append(event)
        run.update({
            "revision": int(run["revision"]) + 1,
            "state": "needs-review",
            "updated_at": now,
            "events": events,
        })
        summary = _summary(run)
        connection.execute(sa.text(
            "UPDATE measurement_runs SET revision = :revision, state = :state, "
            "updated_at = :updated_at, summary_payload = :summary_payload, "
            "payload = :payload WHERE run_id = :run_id"
        ), {
            "run_id": row["run_id"],
            "revision": run["revision"],
            "state": run["state"],
            "updated_at": now,
            "summary_payload": json.dumps(summary, separators=(",", ":"), sort_keys=True),
            "payload": json.dumps(run, separators=(",", ":"), sort_keys=True),
        })
        connection.execute(sa.text(
            "INSERT INTO run_events (run_id, sequence, payload) "
            "VALUES (:run_id, :sequence, :payload)"
        ), {
            "run_id": row["run_id"],
            "sequence": event["sequence"],
            "payload": json.dumps(event, separators=(",", ":"), sort_keys=True),
        })
        updated_runs.add(row["run_id"])


def upgrade() -> None:
    op.add_column("measurement_runs", sa.Column("state", sa.String(40), nullable=True))
    op.add_column("measurement_runs", sa.Column("created_at", sa.String(40), nullable=True))
    op.add_column("measurement_runs", sa.Column("updated_at", sa.String(40), nullable=True))
    op.add_column("measurement_runs", sa.Column("summary_payload", sa.Text(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT run_id, payload FROM measurement_runs")).mappings()
    for row in rows:
        payload = json.loads(row["payload"])
        summary = _summary(payload)
        connection.execute(
            sa.text(
                "UPDATE measurement_runs "
                "SET state = :state, created_at = :created_at, updated_at = :updated_at, "
                "summary_payload = :summary_payload WHERE run_id = :run_id"
            ),
            {
                "run_id": row["run_id"],
                "state": summary["state"],
                "created_at": summary["created_at"],
                "updated_at": summary["updated_at"],
                "summary_payload": json.dumps(summary, separators=(",", ":"), sort_keys=True),
            },
        )
    with op.batch_alter_table("measurement_runs") as batch:
        batch.alter_column("state", existing_type=sa.String(40), nullable=False)
        batch.alter_column("created_at", existing_type=sa.String(40), nullable=False)
        batch.alter_column("updated_at", existing_type=sa.String(40), nullable=False)
        batch.alter_column("summary_payload", existing_type=sa.Text(), nullable=False)
    op.drop_index("ix_measurement_runs_owner_updated", table_name="measurement_runs")
    op.create_index(
        "ix_measurement_runs_owner_updated",
        "measurement_runs",
        ["owner_key", "updated_at", "run_id"],
    )

    op.add_column("workflow_jobs", sa.Column("lease_token", sa.String(64), nullable=True))
    op.add_column("workflow_jobs", sa.Column("policy_id", sa.String(100), nullable=True))
    op.add_column("workflow_jobs", sa.Column("policy_hash", sa.String(64), nullable=True))
    op.add_column("workflow_jobs", sa.Column("execution_principal_id", sa.String(200), nullable=True))
    op.add_column("workflow_jobs", sa.Column("execution_authorization_id", sa.String(64), nullable=True))
    op.add_column("workflow_jobs", sa.Column("operation_ceiling", sa.Integer(), nullable=True))
    _interrupt_legacy_jobs(connection)
    op.create_index(
        "ix_workflow_jobs_dispatch",
        "workflow_jobs",
        ["state", "policy_id", "policy_hash", "owner_key", "created_at"],
    )
    op.add_column("operation_claims", sa.Column("checkpoint_payload", sa.Text(), nullable=True))
    op.add_column(
        "artifacts",
        sa.Column("kind", sa.String(40), nullable=False, server_default="measurement"),
    )
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("uq_artifacts_run_hash", type_="unique")
        batch.create_unique_constraint(
            "uq_artifacts_run_kind_hash",
            ["run_id", "kind", "content_hash"],
        )
    op.create_index("ix_artifacts_run_kind", "artifacts", ["run_id", "owner_key", "kind", "created_at"])

    op.create_table(
        "workflow_admission_lock",
        sa.Column("lock_id", sa.Integer(), primary_key=True),
    )
    connection.execute(sa.text("INSERT INTO workflow_admission_lock (lock_id) VALUES (1)"))

    op.create_table(
        "run_create_requests",
        sa.Column("owner_key", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("measurement_runs.run_id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "artifact_create_requests",
        sa.Column("owner_key", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "artifact_id",
            sa.String(64),
            sa.ForeignKey("artifacts.artifact_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.String(40), nullable=False),
    )

    op.create_table(
        "agent_execution_authorizations",
        sa.Column("authorization_id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("measurement_runs.run_id"),
            nullable=False,
        ),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(200), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("run_revision", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("policy_id", sa.String(100), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("operation_ceiling", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("consumed_at", sa.String(40), nullable=True),
        sa.Column("consumed_by_job_id", sa.String(64), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_agent_authorizations_principal_run",
        "agent_execution_authorizations",
        ["owner_key", "principal_id", "run_id", "stage", "expires_at"],
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(200), primary_key=True),
        sa.Column("current_job_id", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_index(
        "ix_agent_authorizations_principal_run",
        table_name="agent_execution_authorizations",
    )
    op.drop_table("agent_execution_authorizations")
    op.drop_table("artifact_create_requests")
    op.drop_table("run_create_requests")
    op.drop_table("workflow_admission_lock")
    op.drop_index("ix_artifacts_run_kind", table_name="artifacts")
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("uq_artifacts_run_kind_hash", type_="unique")
        batch.create_unique_constraint(
            "uq_artifacts_run_hash",
            ["run_id", "content_hash"],
        )
    op.drop_column("artifacts", "kind")
    op.drop_column("operation_claims", "checkpoint_payload")
    op.drop_index("ix_workflow_jobs_dispatch", table_name="workflow_jobs")
    op.drop_column("workflow_jobs", "operation_ceiling")
    op.drop_column("workflow_jobs", "execution_authorization_id")
    op.drop_column("workflow_jobs", "execution_principal_id")
    op.drop_column("workflow_jobs", "policy_hash")
    op.drop_column("workflow_jobs", "policy_id")
    op.drop_column("workflow_jobs", "lease_token")
    op.drop_index("ix_measurement_runs_owner_updated", table_name="measurement_runs")
    op.create_index(
        "ix_measurement_runs_owner_updated",
        "measurement_runs",
        ["owner_key", "run_id"],
    )
    with op.batch_alter_table("measurement_runs") as batch:
        batch.drop_column("summary_payload")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.drop_column("state")
