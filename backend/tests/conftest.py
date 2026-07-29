import os

# Settings is instantiated at import time in app.config, so required env vars
# must be set before any app module is imported.
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost:5173"]')
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_chess_coach.db")
