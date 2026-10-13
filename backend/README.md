# AI Telemetry Backend (Centralized Ingestion Gateway)

The centralized backend service for the **AI Telemetry Collector** platform, built with **FastAPI**, **SQLAlchemy 2.0 (Async)**, and **PostgreSQL (asyncpg)**.

---

## 1. Overview

The backend service provides:
- High-throughput, asynchronous API foundation.
- Centralized configuration management using `pydantic-settings`.
- Asynchronous database connection pooling via SQLAlchemy 2.0 and `asyncpg`.
- Lightweight health checks distinguishing application runtime health from database connectivity.
- Enterprise security posture: restrictive CORS defaults, masked credential logs, and automated HTTP security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`).

---

## 2. Directory Structure

```text
backend/
├── pyproject.toml              # Standalone dependencies for backend service
├── alembic.ini                 # Database migration configuration
├── alembic/                    # Async Alembic migrations
│   ├── env.py                  # Migration runner using app configuration
│   └── versions/               # Schema migration scripts
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application factory and lifespan manager
│   ├── config.py               # Typed Pydantic settings module
│   ├── database.py             # Asynchronous SQLAlchemy engine & connection pool
│   ├── dependencies.py         # Request-scoped database sessions & auth dependencies
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── health.py       # GET /api/v1/health endpoint
│   ├── models/                 # SQLAlchemy 2.0 declarative database models
│   ├── schemas/                # Pydantic v2 validation & response schemas
│   └── services/               # Core business logic services
├── tests/                      # Asynchronous pytest suite
│   ├── conftest.py             # HTTPX AsyncClient fixtures
│   ├── test_app.py             # Security headers, CORS, and lifecycle tests
│   ├── test_config.py          # Configuration and credential masking tests
│   └── test_health.py          # Health check and database failure tests
├── .env.example                # Safe environment variable template
└── README.md                   # This documentation
```

---

## 3. Getting Started

### Prerequisites
- Python 3.11+ (Python 3.11 – 3.14 supported)
- PostgreSQL 15+ (optional for basic health check; required for persistence)

### 1. Install Dependencies
From the repository root or `backend/` directory:
```bash
pip install -e backend/
```
Or install directly:
```bash
pip install fastapi uvicorn pydantic-settings sqlalchemy asyncpg alembic httpx pytest pytest-asyncio
```

### 2. Configure Environment
Copy the example environment file:
```bash
cp backend/.env.example backend/.env
```
Edit `backend/.env` with your configuration:
```env
APP_ENV=development
DEBUG=false
LOG_LEVEL=INFO
API_V1_PREFIX=/api/v1
DATABASE_URL=postgresql+asyncpg://telemetry_user:secret@localhost:5432/telemetry_db
CORS_ORIGINS=http://localhost:3000
```

### 3. Run the Development Server
```bash
# Run using uvicorn
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Interactive API documentation will be available at:
- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) (disabled in production)
- ReDoc: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## 4. Endpoints

### Service Health Check
```http
GET /api/v1/health
```

#### Responses
- **HTTP 200 OK** (Database connected or not configured):
  ```json
  {
    "status": "healthy",
    "app": "healthy",
    "database": "connected",
    "version": "0.1.0",
    "environment": "development"
  }
  ```
- **HTTP 503 Service Unavailable** (Database configured but unreachable):
  ```json
  {
    "status": "unhealthy",
    "app": "healthy",
    "database": "disconnected",
    "version": "0.1.0",
    "environment": "development"
  }
  ```

---

## 5. Database Migrations (Alembic)

Alembic is configured for async operations using the project's settings:

```bash
# Run from backend directory
cd backend

# Create a new migration revision
alembic revision --autogenerate -m "create_initial_schema"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

---

## 6. Running Tests

Run the asynchronous test suite:
```bash
pytest backend/tests -v
```

All tests execute with in-memory or mocked database fixtures without requiring an external PostgreSQL instance.
