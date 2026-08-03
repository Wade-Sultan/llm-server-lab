from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text

# Data structures


@dataclass
class RLSPolicy:
    """
    Describes a single RLS policy.

    Attributes:
        name:       Unique policy name (within the table).
        table:      Table the policy applies to.
        command:    SQL command(s) the policy covers.
        using:      USING expression — filters which *existing* rows are visible.
        with_check: WITH CHECK expression — filters which rows can be *written*.
                    Defaults to the USING expression if not specified.
        role:       Postgres role the policy targets. Defaults to 'authenticated'
                    (Supabase's role for logged-in users).
        schema:     Schema the table lives in. Defaults to 'public'.
        permissive: Whether the policy is PERMISSIVE (default) or RESTRICTIVE.
    """

    name: str
    table: str
    command: Literal["ALL", "SELECT", "INSERT", "UPDATE", "DELETE"] = "ALL"
    using: str = ""
    with_check: str = ""
    role: str = "authenticated"
    schema: str = "public"
    permissive: bool = True


# Enable / disable RLS


def enable_rls(op, table: str, *, schema: str = "public", force: bool = True) -> None:
    qualified = f'"{schema}"."{table}"'
    op.execute(text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY"))
    if force:
        op.execute(text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY"))


def disable_rls(op, table: str, *, schema: str = "public") -> None:
    """Disable RLS on a table (for downgrade migrations)."""
    qualified = f'"{schema}"."{table}"'
    op.execute(text(f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY"))


# Add / drop policies


def add_policy(op, policy: RLSPolicy) -> None:
    qualified = f'"{policy.schema}"."{policy.table}"'
    kind = "PERMISSIVE" if policy.permissive else "RESTRICTIVE"

    parts = [
        f'CREATE POLICY "{policy.name}" ON {qualified}',
        f"  AS {kind}",
        f"  FOR {policy.command}",
        f"  TO {policy.role}",
    ]

    if policy.using:
        parts.append(f"  USING ({policy.using})")

    check_expr = policy.with_check or policy.using
    if check_expr:
        parts.append(f"  WITH CHECK ({check_expr})")

    sql = "\n".join(parts)
    op.execute(text(sql))


def drop_policy(op, policy_name: str, table: str, *, schema: str = "public") -> None:
    """Drop an RLS policy (for downgrade migrations)."""
    qualified = f'"{schema}"."{table}"'
    op.execute(text(f'DROP POLICY IF EXISTS "{policy_name}" ON {qualified}'))


# Pre-built policy templates


def owner_read_write_policy(
    table: str,
    *,
    owner_column: str = "owner_id",
    name: str | None = None,
    schema: str = "public",
) -> RLSPolicy:
    return RLSPolicy(
        name=name or f"{table}_owner_all",
        table=table,
        command="ALL",
        using=f"{owner_column} = auth.uid()",
        schema=schema,
    )


def authenticated_read_policy(
    table: str,
    *,
    name: str | None = None,
    schema: str = "public",
) -> RLSPolicy:
    return RLSPolicy(
        name=name or f"{table}_authenticated_select",
        table=table,
        command="SELECT",
        using="true",
        schema=schema,
    )


def service_role_bypass_policy(
    table: str,
    *,
    name: str | None = None,
    schema: str = "public",
) -> RLSPolicy:
    return RLSPolicy(
        name=name or f"{table}_service_role_all",
        table=table,
        command="ALL",
        using="true",
        role="service_role",
        schema=schema,
    )
