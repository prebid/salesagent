"""allow_pixel_ratio_in_format_ids

core/format-id.json declares SIX properties -- agent_url, id, width, height, duration_ms,
pixel_ratio -- with only agent_url and id required. ``validate_format_ids`` allowed the
first five and rejected the sixth, so the database refused a product carrying a property
the pinned spec defines and marks optional:

    FormatId has invalid properties.
      Allowed: agent_url, id, width, height, duration_ms
      Got: {id,width,height,agent_url,pixel_ratio}

Only the e2e_rest leg could see it, and that is structural rather than luck:
tests/conftest_db.py builds the integration schema with Base.metadata.create_all, so this
function does not exist in the integration database at all (salesagent-3cs7o.42). e2e_rest
drives a real INSERT against a migrated database, which is the only place the constraint
runs. salesagent-knd96.

pixel_ratio is validated the way the other three optional parameters are -- present-only,
numeric, positive -- rather than left unchecked. A widened allowlist with no type rule
would accept pixel_ratio: "large", which the previous shape would have refused outright.

Revision ID: a7c31e0f45b2
Revises: 08cd415b3d91
Create Date: 2026-10-16

"""

import re
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c31e0f45b2"
down_revision: str | Sequence[str] | None = "08cd415b3d91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The six properties ``core/format-id.json`` declares. Retyped here because PL/pgSQL
#: cannot read the pinned schema, and that is exactly how this function drifted from the
#: spec in the first place.
#:
#: NOTHING DETECTS THE SPEC ADDING A SEVENTH PROPERTY. Measured: no test compares this
#: list against the pin. The e2e_rest rows grade the BEHAVIOUR -- a creative at the wrong
#: asset density is refused, and one silent about density is refused by a product that
#: fixes one (BR-UC-FORMAT-SATISFACTION.feature:421 and :428) -- so a seller authoring a
#: seventh property gets refused by this function and finds out then. Two earlier attempts
#: at a drift detector were removed: one asserted the migration's source text with a
#: regex, the other mutated a module-scoped database to test alembic. Neither graded the
#: spec. If a third is written it reads the pin and this constant and touches no database.
_ALLOWED = "ARRAY['agent_url', 'id', 'width', 'height', 'duration_ms', 'pixel_ratio']"


def _function(allowed_keys: str, with_pixel_ratio: bool) -> str:
    """The validator, parameterised only by its allowlist and the pixel_ratio clause.

    The refusal message renders the SAME array it enforces. It used to take the prose list as
    a second parameter, hand-typed at both call sites, so updating the array alone would have
    left the message naming a set the function no longer refused on.
    """
    allowed_text = ", ".join(re.findall(r"'([^']+)'", allowed_keys))
    pixel_ratio_clause = (
        """
            -- Validate pixel_ratio if present (must be positive number)
            IF format_id ? 'pixel_ratio' THEN
                IF jsonb_typeof(format_id->'pixel_ratio') != 'number' THEN
                    RAISE EXCEPTION 'FormatId.pixel_ratio must be a number, got: %',
                        jsonb_typeof(format_id->'pixel_ratio');
                END IF;
                IF (format_id->>'pixel_ratio')::numeric <= 0 THEN
                    RAISE EXCEPTION 'FormatId.pixel_ratio must be positive, got: %',
                        format_id->>'pixel_ratio';
                END IF;
            END IF;
"""
        if with_pixel_ratio
        else ""
    )
    return f"""
    CREATE OR REPLACE FUNCTION validate_format_ids(format_ids_json jsonb)
    RETURNS boolean AS $$
    DECLARE
        format_id jsonb;
        keys text[];
        allowed_keys text[] := {allowed_keys};
    BEGIN
        -- Must be array
        IF jsonb_typeof(format_ids_json) != 'array' THEN
            RAISE EXCEPTION 'format_ids must be a JSON array, got: %', jsonb_typeof(format_ids_json);
        END IF;

        -- Validate each FormatId object
        FOR format_id IN SELECT * FROM jsonb_array_elements(format_ids_json)
        LOOP
            -- Must be object
            IF jsonb_typeof(format_id) != 'object' THEN
                RAISE EXCEPTION 'Each format_id must be an object, got: %', jsonb_typeof(format_id);
            END IF;

            SELECT array_agg(key) INTO keys FROM jsonb_object_keys(format_id) key;

            -- Check required keys exist
            IF NOT (keys @> ARRAY['agent_url', 'id']) THEN
                RAISE EXCEPTION 'FormatId must have "agent_url" and "id" properties, got: %', keys;
            END IF;

            -- Check no unexpected keys
            IF NOT (keys <@ allowed_keys) THEN
                RAISE EXCEPTION 'FormatId has invalid properties. Allowed: {allowed_text}. Got: %', keys;
            END IF;

            -- agent_url must be string
            IF jsonb_typeof(format_id->'agent_url') != 'string' THEN
                RAISE EXCEPTION 'FormatId.agent_url must be a string, got: %', jsonb_typeof(format_id->'agent_url');
            END IF;

            -- id must be string
            IF jsonb_typeof(format_id->'id') != 'string' THEN
                RAISE EXCEPTION 'FormatId.id must be a string, got: %', jsonb_typeof(format_id->'id');
            END IF;

            -- Validate agent_url is not empty
            IF length(format_id->>'agent_url') = 0 THEN
                RAISE EXCEPTION 'FormatId.agent_url cannot be empty string';
            END IF;

            -- Validate id is not empty
            IF length(format_id->>'id') = 0 THEN
                RAISE EXCEPTION 'FormatId.id cannot be empty string';
            END IF;

            -- Validate width if present (must be positive integer)
            IF format_id ? 'width' THEN
                IF jsonb_typeof(format_id->'width') != 'number' THEN
                    RAISE EXCEPTION 'FormatId.width must be a number, got: %', jsonb_typeof(format_id->'width');
                END IF;
                IF (format_id->>'width')::numeric <= 0 THEN
                    RAISE EXCEPTION 'FormatId.width must be positive, got: %', format_id->>'width';
                END IF;
            END IF;

            -- Validate height if present (must be positive integer)
            IF format_id ? 'height' THEN
                IF jsonb_typeof(format_id->'height') != 'number' THEN
                    RAISE EXCEPTION 'FormatId.height must be a number, got: %', jsonb_typeof(format_id->'height');
                END IF;
                IF (format_id->>'height')::numeric <= 0 THEN
                    RAISE EXCEPTION 'FormatId.height must be positive, got: %', format_id->>'height';
                END IF;
            END IF;

            -- Validate duration_ms if present (must be positive number)
            IF format_id ? 'duration_ms' THEN
                IF jsonb_typeof(format_id->'duration_ms') != 'number' THEN
                    RAISE EXCEPTION 'FormatId.duration_ms must be a number, got: %',
                        jsonb_typeof(format_id->'duration_ms');
                END IF;
                IF (format_id->>'duration_ms')::numeric <= 0 THEN
                    RAISE EXCEPTION 'FormatId.duration_ms must be positive, got: %', format_id->>'duration_ms';
                END IF;
            END IF;
{pixel_ratio_clause}
        END LOOP;

        RETURN true;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE;
    """


def upgrade() -> None:
    """Allow the pinned ``pixel_ratio`` property, validated like its three siblings."""
    op.execute(_function(_ALLOWED, with_pixel_ratio=True))


def downgrade() -> None:
    """Restore the five-property allowlist, rejecting ``pixel_ratio`` again."""
    op.execute(_function("ARRAY['agent_url', 'id', 'width', 'height', 'duration_ms']", with_pixel_ratio=False))
