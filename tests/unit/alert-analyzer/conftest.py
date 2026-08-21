import os
import sys

# Порядок важен: у alert-analyzer свои одноимённые metrics/store/models,
# они должны победить модули sre-agent. /srv/src нужен для пакета common.
sys.path.insert(0, "/srv/src/alert-analyzer")
sys.path.insert(1, "/srv/src")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
