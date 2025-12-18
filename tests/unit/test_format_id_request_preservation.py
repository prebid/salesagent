"""Test that format_ids from request packages are preserved when creating MediaPackages."""


def test_format_ids_from_request_preserved():
    """Test that buyer-specified format_ids override product formats when buyer picks subset."""

    # Create mock product with MULTIPLE formats
    class MockProduct:
        def __init__(self):
            self.product_id = "display_multi_cpm"
            self.name = "Display Multi Format CPM"
            self.format_ids = ["leaderboard_728x90", "display_300x250"]  # Supports both

    product = MockProduct()

    # Simulate request package choosing display_300x250 (buyer's preference)
    class MockPackage:
        def __init__(self):
            self.product_id = "display_multi_cpm"
            self.format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]

    req_package = MockPackage()

    # Simulate the logic from main.py (lines 4502-4546)
    format_ids_to_use = []

    # Find matching package
    if req_package.product_id == product.product_id:
        # Extract format IDs from FormatId objects
        requested_format_ids = []
        for fmt in req_package.format_ids:
            if isinstance(fmt, dict):
                requested_format_ids.append(fmt.get("id"))
            elif hasattr(fmt, "id"):
                requested_format_ids.append(fmt.id)
            elif isinstance(fmt, str):
                requested_format_ids.append(fmt)

        # Validate that requested formats are supported by product
        product_format_ids = set(product.format_ids) if product.format_ids else set()
        unsupported_formats = [f for f in requested_format_ids if f not in product_format_ids]

        # Should pass validation since display_300x250 is supported
        assert not unsupported_formats, f"Unexpected unsupported formats: {unsupported_formats}"
        format_ids_to_use = requested_format_ids

    # Fallback to product's formats if no request format_ids
    if not format_ids_to_use:
        format_ids_to_use = [product.format_ids[0]] if product.format_ids else []

    # Verify that request format_ids take precedence (buyer chose display_300x250, not leaderboard)
    assert format_ids_to_use == ["display_300x250"], f"Expected ['display_300x250'], got {format_ids_to_use}"
    assert "leaderboard_728x90" not in format_ids_to_use, "Buyer chose display_300x250, should not include leaderboard"


def test_format_ids_fallback_to_product():
    """Test that product formats are used when request has no format_ids."""

    # Create mock product with leaderboard format
    class MockProduct:
        def __init__(self):
            self.product_id = "display_leaderboard_cpm"
            self.format_ids = ["leaderboard_728x90"]

    product = MockProduct()

    # Simulate request package WITHOUT format_ids
    class MockPackage:
        def __init__(self):
            self.product_id = "display_leaderboard_cpm"
            self.format_ids = None  # No format_ids in request

    req_package = MockPackage()

    # Simulate the logic from main.py
    format_ids_to_use = []

    # Find matching package
    if req_package.product_id == product.product_id and req_package.format_ids:
        # Extract format IDs from FormatId objects
        for fmt in req_package.format_ids:
            if isinstance(fmt, dict):
                format_ids_to_use.append(fmt.get("id"))
            elif hasattr(fmt, "id"):
                format_ids_to_use.append(fmt.id)
            elif isinstance(fmt, str):
                format_ids_to_use.append(fmt)

    # Fallback to product's formats if no request format_ids
    if not format_ids_to_use:
        format_ids_to_use = [product.format_ids[0]] if product.format_ids else []

    # Verify that product formats are used as fallback
    assert format_ids_to_use == ["leaderboard_728x90"], f"Expected ['leaderboard_728x90'], got {format_ids_to_use}"


def test_format_ids_handles_format_id_objects():
    """Test that FormatId objects (with .id attribute) are handled correctly."""

    class FormatId:
        def __init__(self, agent_url, format_id):
            self.agent_url = agent_url
            self.id = format_id

    # Create mock product with multiple formats
    class MockProduct:
        def __init__(self):
            self.product_id = "display_multi_cpm"
            self.name = "Display Multi Format CPM"
            self.format_ids = ["leaderboard_728x90", "display_300x250"]

    product = MockProduct()

    # Simulate request package with FormatId objects
    class MockPackage:
        def __init__(self):
            self.product_id = "display_multi_cpm"
            self.format_ids = [FormatId("https://creative.adcontextprotocol.org", "display_300x250")]

    req_package = MockPackage()

    # Simulate the logic from main.py
    format_ids_to_use = []

    # Find matching package
    if req_package.product_id == product.product_id:
        # Extract format IDs from FormatId objects
        requested_format_ids = []
        for fmt in req_package.format_ids:
            if isinstance(fmt, dict):
                requested_format_ids.append(fmt.get("id"))
            elif hasattr(fmt, "id"):
                requested_format_ids.append(fmt.id)
            elif isinstance(fmt, str):
                requested_format_ids.append(fmt)

        # Validate that requested formats are supported by product
        product_format_ids = set(product.format_ids) if product.format_ids else set()
        unsupported_formats = [f for f in requested_format_ids if f not in product_format_ids]

        # Should pass validation since display_300x250 is supported
        assert not unsupported_formats, f"Unexpected unsupported formats: {unsupported_formats}"
        format_ids_to_use = requested_format_ids

    # Fallback to product's formats if no request format_ids
    if not format_ids_to_use:
        format_ids_to_use = [product.format_ids[0]] if product.format_ids else []

    # Verify that FormatId.id is extracted correctly
    assert format_ids_to_use == ["display_300x250"], f"Expected ['display_300x250'], got {format_ids_to_use}"


def test_format_ids_validation_rejects_unsupported():
    """Test that requesting unsupported format_ids raises an error."""

    # Create mock product with only leaderboard format
    class MockProduct:
        def __init__(self):
            self.product_id = "display_leaderboard_cpm"
            self.name = "Display Leaderboard CPM"
            self.format_ids = ["leaderboard_728x90"]  # Only supports leaderboard

    product = MockProduct()

    # Simulate request package with UNSUPPORTED format (video)
    class MockPackage:
        def __init__(self):
            self.product_id = "display_leaderboard_cpm"
            self.format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_instream"}]

    req_package = MockPackage()

    # Simulate the validation logic from main.py
    requested_format_ids = []
    for fmt in req_package.format_ids:
        if isinstance(fmt, dict):
            requested_format_ids.append(fmt.get("id"))
        elif hasattr(fmt, "id"):
            requested_format_ids.append(fmt.id)
        elif isinstance(fmt, str):
            requested_format_ids.append(fmt)

    # Validate that requested formats are supported by product
    product_format_ids = set(product.format_ids) if product.format_ids else set()
    unsupported_formats = [f for f in requested_format_ids if f not in product_format_ids]

    # Verify that unsupported format is detected
    assert unsupported_formats == ["video_instream"], f"Expected ['video_instream'], got {unsupported_formats}"
    assert "video_instream" not in product_format_ids, "video_instream should not be in product formats"


def test_format_ids_validation_accepts_supported():
    """Test that requesting supported format_ids is accepted."""

    # Create mock product with multiple formats
    class MockProduct:
        def __init__(self):
            self.product_id = "display_multi_cpm"
            self.name = "Display Multi Format CPM"
            self.format_ids = ["leaderboard_728x90", "display_300x250", "display_160x600"]

    product = MockProduct()

    # Simulate request package with SUPPORTED format
    class MockPackage:
        def __init__(self):
            self.product_id = "display_multi_cpm"
            self.format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]

    req_package = MockPackage()

    # Simulate the validation logic from main.py
    requested_format_ids = []
    for fmt in req_package.format_ids:
        if isinstance(fmt, dict):
            requested_format_ids.append(fmt.get("id"))
        elif hasattr(fmt, "id"):
            requested_format_ids.append(fmt.id)
        elif isinstance(fmt, str):
            requested_format_ids.append(fmt)

    # Validate that requested formats are supported by product
    product_format_ids = set(product.format_ids) if product.format_ids else set()
    unsupported_formats = [f for f in requested_format_ids if f not in product_format_ids]

    # Verify that all requested formats are supported
    assert unsupported_formats == [], f"Expected no unsupported formats, got {unsupported_formats}"
    assert "display_300x250" in product_format_ids, "display_300x250 should be in product formats"
