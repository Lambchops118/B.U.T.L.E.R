"""phase 7 action lifecycle hardening

Revision ID: e7c11f9a4b2d
Revises: 4d268f4eae02
Create Date: 2026-07-16

Adds the explicit validated lifecycle status and database-enforced command ID
uniqueness. This is a separate revision so databases that already applied the
initial Phase 7 migration upgrade cleanly.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e7c11f9a4b2d"
down_revision: Union[str, None] = "4d268f4eae02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUSES = (
    "requested",
    "rejected",
    "awaiting_confirmation",
    "approved",
    "dispatched",
    "acknowledged",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
)
_NEW_STATUSES = (
    "requested",
    "rejected",
    "validated",
    "awaiting_confirmation",
    "approved",
    "dispatched",
    "acknowledged",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
)


def _status_expression(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_action_requests_status_valid"), "action_requests", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_action_requests_status_valid"),
        "action_requests",
        _status_expression(_NEW_STATUSES),
    )
    op.drop_index("ix_action_requests_command_id", table_name="action_requests")
    op.create_unique_constraint(
        op.f("uq_action_requests_command_id"), "action_requests", ["command_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_action_requests_command_id"), "action_requests", type_="unique"
    )
    op.create_index(
        "ix_action_requests_command_id",
        "action_requests",
        ["command_id"],
        unique=False,
    )
    op.drop_constraint(
        op.f("ck_action_requests_status_valid"), "action_requests", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_action_requests_status_valid"),
        "action_requests",
        _status_expression(_OLD_STATUSES),
    )
