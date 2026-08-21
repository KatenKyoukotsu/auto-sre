import os
import sys

sys.path.insert(0, "/srv/src/sre-agent")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
