import os

# Set fake credentials before any gabechoice module is imported so
# Settings() doesn't blow up on missing required fields.
os.environ.setdefault("STEAM_API_KEY", "test_key")
os.environ.setdefault("STEAM_ID_64", "12345678901234567")
