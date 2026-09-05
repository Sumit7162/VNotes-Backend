"""
Test configuration for the Video Notes AI backend.

Uses SQLite in-memory database for fast, isolated tests.
"""

import uuid
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base, get_db
from app.core.config import get_settings
from app.main import app

# Use SQLite in-memory for tests
TEST_DATABASE_URL = "sqlite:///./test.db"

engine = TestEngine = None


@pytest.fixture(scope="session")
def db_engine():
    """Create a fresh SQLite in-memory engine for the test session."""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    """Create a fresh database session for each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(autocommit=False, autoflush=False, bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session) -> Generator[TestClient, None, None]:
    """FastAPI test client with overridden database dependency."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sample_user_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def sample_video_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def mock_auth_token() -> str:
    """Return a mock JWT token for testing.
    
    This is a dev-mode token that bypasses signature verification.
    """
    import jwt
    payload = {
        "sub": "clerk_test_user_123",
        "email": "test@example.com",
        "full_name": "Test User",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(datetime.now(timezone.utc).timestamp()) + 3600,
    }
    return jwt.encode(payload, "test-secret", algorithm="HS256")
