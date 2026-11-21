"""The admin product forms, filled in to the minimum their handlers require.

Every test that posts ``products/add`` or ``products/<id>/edit`` starts from these and
overrides only what it is about, so a field the handler starts requiring is added once.
"""

from __future__ import annotations


def minimal_product_edit_form(**overrides: object) -> dict[str, object]:
    """The product-edit form at the minimum the handler requires.

    The pricing block is NOT optional: ``edit_product`` refuses a product with no pricing
    option, and a payload that omits it is refused before anything else is read -- which
    would make a refusal test pass for the wrong reason.

    ``delivery_measurement_provider`` is not optional either: the route writes
    ``delivery_measurement`` unconditionally from the field, and the column is NOT NULL,
    so a payload omitting it raises IntegrityError (salesagent-igxy9).
    """
    form: dict[str, object] = {
        "name": "Edited Product",
        "pricing_model_0": "cpm_fixed",
        "currency_0": "USD",
        "rate_0": "10.00",
        "delivery_measurement_provider": "publisher",
    }
    form.update(overrides)
    return form


def minimal_product_create_form(property_tag_selection: str | list[str], **overrides: object) -> dict[str, object]:
    """The product-create form at the minimum the handler requires.

    *property_tag_selection* is the ``"<publisher_domain>:<tag>"`` value the property picker
    submits, or a list of them for a product of several publishers;
    ``tests.factories.core.seed_property_tag_selection`` seeds the rows each names and
    returns it. ``formats: "[]"`` leaves the deprecated arm empty, so a test authors the
    canonical arm through a ``format_options`` override.
    """
    form = minimal_product_edit_form(
        name="Canonically Authored Product",
        description="Authored through the operator form",
        formats="[]",
        property_mode="tags",
        selected_property_tags=property_tag_selection,
    )
    form.update(overrides)
    return form
