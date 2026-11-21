"""The character class shared by principal creation and reporting lookups."""

# The escaped hyphen also works in HTML patterns compiled with the v flag.
PRINCIPAL_ID_CHARACTERS = r"[a-zA-Z0-9_\-]"
