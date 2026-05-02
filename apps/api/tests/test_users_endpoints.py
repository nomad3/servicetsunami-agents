"""Tests for /users endpoints — contract guard for the member directory.

Covers the PR #248 review fix (C1): `GET /users` must NOT leak the
nested Tenant object or raw `is_superuser` flag — those would let any
authenticated tenant member read admin-only state via the JSON payload.
The lean `UserBrief` response model is the contract.
"""
from app.api.v1.users import ProfileUpdate, UserBrief


def test_user_brief_excludes_tenant_relationship():
    """The slim payload schema for the member directory has no `tenant`
    field — nesting the full Tenant on every member row both wastes
    payload size and exposes Tenant fields to non-admin members."""
    fields = UserBrief.model_fields if hasattr(UserBrief, "model_fields") else UserBrief.__fields__
    assert "tenant" not in fields
    assert "tenant_id" not in fields


def test_user_brief_excludes_raw_is_superuser():
    """Admin-only column should not appear in the response — projected
    to a `role` string instead so the SQL flag never crosses the API
    boundary."""
    fields = UserBrief.model_fields if hasattr(UserBrief, "model_fields") else UserBrief.__fields__
    assert "is_superuser" not in fields
    assert "role" in fields


def test_user_brief_role_is_admin_or_member():
    """Role projection is a closed Literal — clients can switch on it
    without worrying about future role string drift."""
    role_field = (UserBrief.model_fields if hasattr(UserBrief, "model_fields") else UserBrief.__fields__)["role"]
    # Pydantic v2 Literal storage:
    annotation = role_field.annotation if hasattr(role_field, "annotation") else role_field.outer_type_
    args_str = str(annotation)
    assert "admin" in args_str
    assert "member" in args_str


def test_profile_update_only_allows_full_name():
    """ProfileUpdate is a closed allow-list. Sending email / is_superuser
    in the payload must NOT mutate them — Pydantic strips unknown fields
    silently. Only `full_name` should be a declared field."""
    fields = ProfileUpdate.model_fields if hasattr(ProfileUpdate, "model_fields") else ProfileUpdate.__fields__
    assert set(fields.keys()) == {"full_name"}


def test_profile_update_full_name_max_length_enforced():
    """Pydantic enforces the max_length=255 on full_name — protects
    against pathological inputs hitting the DB column limit."""
    too_long = "a" * 300
    try:
        ProfileUpdate(full_name=too_long)
        raised = False
    except Exception:
        raised = True
    assert raised, "ProfileUpdate must reject full_name > 255 chars"


def test_profile_update_full_name_empty_or_none_allowed_at_schema_layer():
    """Schema-level: empty string is valid input (handler rejects it).
    None is valid and means "no change". Both should construct
    without raising at the Pydantic layer."""
    # Empty string passes schema (handler returns 422)
    ProfileUpdate(full_name="")
    # None passes schema (means no change)
    ProfileUpdate(full_name=None)
    # Whitespace passes schema (handler returns 422)
    ProfileUpdate(full_name="   ")
