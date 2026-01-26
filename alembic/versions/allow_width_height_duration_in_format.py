"""allow_width_height_duration_in_format_ids

Revision ID: f972939dd331
Revises: f319ed58b321
Create Date: 2026-01-15 10:05:45.647246

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f972939dd331"
down_revision: Union[str, Sequence[str], None] = "f319ed58b321"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema to allow width, height, and duration_ms in FormatId objects."""

    # Update the validate_format_ids function to allow optional width, height, duration_ms
    updated_validation_function = """
    CREATE OR REPLACE FUNCTION validate_format_ids(format_ids_json jsonb)
    RETURNS boolean AS $$
    DECLARE
        format_id jsonb;
        keys text[];
        allowed_keys text[] := ARRAY['agent_url', 'id', 'width', 'height', 'duration_ms'];
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

            -- Must have agent_url and id (required), optionally width, height, duration_ms
            SELECT array_agg(key) INTO keys FROM jsonb_object_keys(format_id) key;

            -- Check required keys exist
            IF NOT (keys @> ARRAY['agent_url', 'id']) THEN
                RAISE EXCEPTION 'FormatId must have "agent_url" and "id" properties, got: %', keys;
            END IF;

            -- Check no unexpected keys
            IF NOT (keys <@ allowed_keys) THEN
                RAISE EXCEPTION 'FormatId has invalid properties. Allowed: agent_url, id, width, height, duration_ms. Got: %', keys;
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
                    RAISE EXCEPTION 'FormatId.duration_ms must be a number, got: %', jsonb_typeof(format_id->'duration_ms');
                END IF;
                IF (format_id->>'duration_ms')::numeric <= 0 THEN
                    RAISE EXCEPTION 'FormatId.duration_ms must be positive, got: %', format_id->>'duration_ms';
                END IF;
            END IF;
        END LOOP;

        RETURN true;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE;
    """

    print("Updating validate_format_ids() to allow width, height, duration_ms...")
    op.execute(updated_validation_function)


def downgrade() -> None:
    """Downgrade schema to original validation (only agent_url and id allowed)."""

    # Restore original validation function that only allows agent_url and id
    original_validation_function = """
    CREATE OR REPLACE FUNCTION validate_format_ids(format_ids_json jsonb)
    RETURNS boolean AS $$
    DECLARE
        format_id jsonb;
        keys text[];
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

            -- Must have exactly 2 keys: agent_url and id
            SELECT array_agg(key) INTO keys FROM jsonb_object_keys(format_id) key;
            IF array_length(keys, 1) != 2 OR NOT (keys @> ARRAY['agent_url', 'id']) THEN
                RAISE EXCEPTION 'FormatId must have exactly "agent_url" and "id" properties, got: %', keys;
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
        END LOOP;

        RETURN true;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE;
    """

    print("Restoring original validate_format_ids() (agent_url and id only)...")
    op.execute(original_validation_function)
