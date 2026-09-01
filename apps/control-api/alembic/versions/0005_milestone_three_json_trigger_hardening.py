"""Harden immutable workflow JSON comparisons.

Revision ID: 0005_m3_json_trigger
Revises: 0004_m3_workflow
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op

revision = "0005_m3_json_trigger"
down_revision = "0004_m3_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION runsigil_immutable_workflow_version() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.organization_id <> OLD.organization_id
             OR NEW.workflow_id <> OLD.workflow_id
             OR NEW.version <> OLD.version
             OR NEW.definition_json::jsonb IS DISTINCT FROM OLD.definition_json::jsonb
             OR NEW.definition_digest <> OLD.definition_digest
             OR NEW.validation_json::jsonb IS DISTINCT FROM OLD.validation_json::jsonb
             OR NEW.created_by <> OLD.created_by THEN
            RAISE EXCEPTION 'RunSigil workflow version content is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )


def downgrade() -> None:
    raise RuntimeError("0005 is intentionally irreversible because it hardens workflow lineage")
