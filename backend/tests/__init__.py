# The tests never read the real Windows vault: a Claude key saved by the user must not change their
# results nor reach a test double (CLAUDE.md). Vault tests use a fake vault instead.
import os

os.environ.setdefault('AURORA_VAULT', '0')
# Nor the local SearXNG (Docker open or closed must not change a result): web tests turn it on explicitly.
os.environ.setdefault('AURORA_SEARXNG_URL', 'off')
# Nor read the pages a search found (a real DNS lookup would leave the PC): tests/test_webpage.py turns it on.
os.environ.setdefault('AURORA_READ_PAGES', 'off')
