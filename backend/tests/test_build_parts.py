"""
Multi-instance build parts: a build may hold several GPUs, drives and fans,
but is still exactly one CPU / board / cooler / PSU / case.

The uniqueness itself is enforced by partial indexes and check constraints in
Postgres, so it isn't reachable from a pure-logic test. What is tested here is
everything that decides *what those constraints say* — the role set, the SQL
predicates generated from it, and the quantity arithmetic callers depend on.
A drift between MULTI_INSTANCE_ROLES and the predicates would silently widen
or narrow the schema on the next migration.
"""

from __future__ import annotations

import pytest

from app.models.pcbuild import (
    IS_MULTI_INSTANCE_ROLE_SQL,
    IS_SINGLETON_ROLE_SQL,
    MULTI_INSTANCE_ROLES,
    REQUIRED_COMPONENT_BY_ROLE,
    BuildComponentRole,
    BuildPart,
)

_SINGLETON_ROLES = set(BuildComponentRole) - MULTI_INSTANCE_ROLES


def test_the_three_repeatable_roles_are_gpu_storage_and_fan():
    assert {r.value for r in MULTI_INSTANCE_ROLES} == {"gpu", "storage", "fan"}


def test_ram_is_not_repeatable():
    """A memory kit is already a multi-module product (ram_groups.modules), so
    a second RAM row would double-count rather than describe a second thing."""
    assert BuildComponentRole.RAM not in MULTI_INSTANCE_ROLES


@pytest.mark.parametrize(
    "role",
    [
        BuildComponentRole.CPU,
        BuildComponentRole.MOTHERBOARD,
        BuildComponentRole.PSU,
        BuildComponentRole.CASE,
        BuildComponentRole.CPU_COOLER,
    ],
)
def test_structural_singletons_stay_singletons(role):
    assert role not in MULTI_INSTANCE_ROLES


def test_predicates_are_derived_from_the_role_set_not_hand_written():
    """The two SQL predicates must partition the role enum exactly. Hand-edited
    copies would drift from MULTI_INSTANCE_ROLES the first time it changes."""
    for role in MULTI_INSTANCE_ROLES:
        assert f"'{role.value}'" in IS_MULTI_INSTANCE_ROLE_SQL
    for role in _SINGLETON_ROLES:
        assert f"'{role.value}'" not in IS_MULTI_INSTANCE_ROLE_SQL

    # Same value list, opposite sense — the singleton index and the quantity
    # check are written against these two and must stay complementary.
    assert IS_SINGLETON_ROLE_SQL == IS_MULTI_INSTANCE_ROLE_SQL.replace(
        "role IN", "role NOT IN"
    )


def test_predicate_value_order_is_stable():
    """Sorted, so the generated DDL string doesn't vary between processes and
    read as schema drift — frozenset iteration order is not stable."""
    assert IS_MULTI_INSTANCE_ROLE_SQL == "role IN ('fan', 'gpu', 'storage')"


def test_migration_predicates_match_the_model():
    """The migration spells the role list out rather than importing it, so that
    an old migration can't change meaning later. This is the check that the two
    copies still agree *today*."""
    import pathlib

    src = (
        pathlib.Path(__file__).parents[1]
        / "app/alembic/versions/b3d5e7f9a1c2_multi_instance_build_parts.py"
    ).read_text()
    assert "_MULTI_ROLES = \"'fan', 'gpu', 'storage'\"" in src


# ---------------------------------------------------------------------------
# Constraint wiring
# ---------------------------------------------------------------------------


def _index(name: str):
    return next(i for i in BuildPart.__table__.indexes if i.name == name)


def _check(name: str):
    return next(c for c in BuildPart.__table__.constraints if c.name == name)


def test_old_table_wide_unique_constraint_is_gone():
    """UNIQUE (build_id, role) is what made multi-GPU impossible; it must not
    survive anywhere in the table definition."""
    names = {c.name for c in BuildPart.__table__.constraints}
    names |= {i.name for i in BuildPart.__table__.indexes}
    assert "uq_pc_build_parts_build_role" not in names


def test_singleton_uniqueness_is_preserved_as_a_partial_index():
    idx = _index("uq_pc_build_parts_singleton_role")
    assert idx.unique
    assert [c.name for c in idx.columns] == ["build_id", "role"]


def test_the_same_part_cannot_repeat_within_a_role():
    idx = _index("uq_pc_build_parts_role_part")
    assert idx.unique
    assert [c.name for c in idx.columns] == ["build_id", "role", "part_id"]


def test_quantity_defaults_to_one_and_must_be_positive():
    quantity = BuildPart.__table__.c.quantity
    assert quantity.nullable is False
    assert quantity.default.arg == 1
    assert quantity.server_default.arg == "1"
    assert _check("ck_pc_build_parts_quantity_positive") is not None


def test_a_singleton_role_cannot_be_smuggled_in_as_quantity_two():
    check = _check("ck_pc_build_parts_singleton_quantity")
    assert IS_MULTI_INSTANCE_ROLE_SQL in str(check.sqltext)


# ---------------------------------------------------------------------------
# Quantity arithmetic
# ---------------------------------------------------------------------------


def test_line_total_multiplies_the_per_unit_price():
    """price_at_build is per unit; four GPUs at $1800 is a $7200 line."""
    part = BuildPart(role=BuildComponentRole.GPU, price_at_build=180_000, quantity=4)
    assert part.line_total_cents == 720_000


def test_line_total_treats_an_unset_quantity_as_one():
    """quantity is populated by a column default at flush time, so it is still
    None on an in-memory instance that hasn't been persisted."""
    part = BuildPart(role=BuildComponentRole.GPU, price_at_build=180_000)
    assert part.quantity is None
    assert part.line_total_cents == 180_000


def test_line_total_is_none_when_the_part_is_unpriced():
    """Distinguishable from zero: an unpriced row must not silently contribute
    nothing to a build total that claims to be complete."""
    part = BuildPart(role=BuildComponentRole.GPU, price_at_build=None, quantity=2)
    assert part.line_total_cents is None


def test_role_still_seeds_required_component():
    """Pre-existing behaviour; the constraint rework must not disturb it."""
    part = BuildPart(role=BuildComponentRole.CPU)
    assert part.required_component is REQUIRED_COMPONENT_BY_ROLE[BuildComponentRole.CPU]

    optional = BuildPart(role=BuildComponentRole.GPU)
    assert optional.required_component is False
