# FeedForge Backend

A FastAPI-based backend service for the FeedForge platform, designed to manage content campaigns, scoring, and generation using AI-powered features with OpenRouter API integration.

## Overview

FeedForge Backend provides a robust API for managing marketing campaigns with intelligent content generation and scoring capabilities.

The service integrates with large language models through OpenRouter to enable AI-powered content operations while maintaining campaign memory and context.

## Features

- **Campaign Management**: Create, retrieve, and manage marketing campaigns with custom voice, target audience personas, and platform-specific rules.
- **Content Scoring**: Evaluate draft content against campaign parameters using intelligent scoring algorithms.
- **Content Generation**: Generate campaign-specific content using AI models with context awareness.
- **Content Approval Workflow**: Submit, approve, or reject content with feedback and brand scoring.
- **Campaign Memory**: Maintain persistent conversation history and context for each campaign.
- **CORS Support**: Flexible cross-origin resource sharing configuration for frontend integration.
- **Database Persistence**: SQLite-based storage for campaigns, content, and metadata.

## Technology Stack

- **FastAPI 0.115.6** - Modern Python web framework
- **Uvicorn 0.32.1** - ASGI server
- **Pydantic 2.10.3** - Data validation and settings management
- **Python 3.12.10**
- **SQLite** - Data persistence
- **OpenRouter API** - LLM integration

## Project Structure

```text
feed-forge-backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application and middleware setup
│   ├── config.py            # Configuration management and environment variables
│   ├── database.py          # Database initialization and operations
│   ├── schemas.py           # Pydantic models for request/response validation
│   ├── llm.py               # OpenRouter LLM integration
│   ├── generation.py        # Content generation logic
│   ├── scoring.py           # Content scoring algorithms
│   ├── memory.py            # Campaign context and conversation memory
│   ├── agent_files.py       # File management for AI agents
│   ├── text_utils.py        # Text processing utilities
│   └── routers/
│       ├── __init__.py
│       ├── campaigns.py     # Campaign management endpoints
│       ├── content.py       # Content operations endpoints
│       └── health.py        # Health check endpoint
├── requirements.txt         # Python dependencies
├── render.yaml              # Render.com deployment configuration
├── .env.example             # Environment variables template
├── .gitignore               # Git ignore rules
└── .python-version          # Python version specification
```

## Installation

### Prerequisites

Make sure you have the following installed:

- Python 3.12.10
- pip or any preferred Python package manager

### Setup

Clone the repository:

```bash
git clone https://github.com/hksharma2004/feed-forge-backend.git
cd feed-forge-backend
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate the virtual environment:

```bash
# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create an environment file:

```bash
cp .env.example .env
```

Update `.env` with your configuration:

```env
OPENROUTER_API_KEY=your_api_key_here
OPENROUTER_MODEL=openrouter/owl-alpha
OPENROUTER_SITE_URL=http://localhost:3000
OPENROUTER_APP_NAME=FeedForge

CORS_ORIGINS=http://localhost:3000,http://localhost:3001
CORS_ORIGIN_REGEX=https?://(localhost|127\.0\.0\.1):\d+

DATA_DIR=./data
DATABASE_PATH=./data/feedforge.db
CAMPAIGNS_DIR=./data/campaigns
```

## Running the Application

### Local Development

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at:

```text
http://localhost:8000
```

API documentation:

```text
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
```

### Production

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API Endpoints

### Health Check

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Check service health status |

### Campaigns

| Method | Endpoint | Description |
|---|---|---|
| POST | `/campaigns/` | Create a new campaign |
| GET | `/campaigns/` | List all campaigns |
| GET | `/campaigns/{campaign_id}` | Get campaign details |

### Content Operations

| Method | Endpoint | Description |
|---|---|---|
| POST | `/content/score` | Score draft content against campaign parameters |
| POST | `/content/generate` | Generate new content for a campaign |
| POST | `/content/approve` | Approve and save content to campaign history |
| POST | `/content/reject` | Reject content and record feedback |

## Configuration

### Environment Variables

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | API key for OpenRouter service |
| `OPENROUTER_MODEL` | LLM model to use |
| `OPENROUTER_TIMEOUT_SECONDS` | Request timeout in seconds |
| `OPENROUTER_MAX_TOKENS` | Maximum tokens per response |
| `CORS_ORIGINS` | Comma-separated list of allowed origins |
| `CORS_ORIGIN_REGEX` | Regex pattern for dynamic origin validation |
| `DATA_DIR` | Directory for data storage |
| `DATABASE_PATH` | SQLite database file path |
| `CAMPAIGNS_DIR` | Directory for campaign files |

## Deployment

### Render.com

The project includes a `render.yaml` file for deployment on Render.

Example deployment commands:

```yaml
buildCommand: pip install -r requirements.txt
startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Key deployment settings:

- Python 3.12.10
- Free tier compatible
- Persistent data directory configuration
- Production CORS settings with custom domain support

## Development

### Code Structure

- **Schemas**: Pydantic models define request and response contracts.
- **Routers**: FastAPI routers organize endpoints by resource type.
- **Database**: SQLite operations handle persistence.
- **LLM Integration**: OpenRouter API wrapper manages AI operations.
- **Memory**: Campaign context management supports multi-turn interactions.
- **Scoring**: Campaign rule evaluation and content assessment.

### Adding New Features

1. Define request and response schemas in `app/schemas.py`.
2. Create router endpoints in the `app/routers/` directory.
3. Implement business logic in the appropriate modules.
4. Register the router in `app/main.py`.
5. Test the feature locally before deployment.

## Testing

For manual API testing:

- Use Swagger UI at `/docs`
- Use ReDoc at `/redoc`
- Use tools like Postman or curl
- Check deployment behavior using `render.yaml`

Example curl request:

```bash
curl http://localhost:8000/health
```

## Database

The application uses SQLite for data persistence.

The database is automatically initialized on startup if it does not already exist.

| Item | Description |
|---|---|
| Location | Configured through the `DATABASE_PATH` environment variable |
| Schema | Created automatically on first run |
| Data | Stored inside the configured `DATA_DIR` |

## CORS Configuration

CORS is configured to support multiple origins:

- Static origins through `CORS_ORIGINS`
- Dynamic origins through `CORS_ORIGIN_REGEX`
- Credentials support enabled
- All HTTP methods and headers allowed

## Error Handling

The API provides standard HTTP status codes and error responses.

| Status Code | Meaning |
|---|---|
| `200` | Successful request |
| `400` | Invalid request data |
| `404` | Resource not found |
| `500` | Server error |

## Contributing

To contribute:

1. Fork the repository.
2. Create a feature branch.
3. Make your changes.
4. Test thoroughly.
5. Submit a pull request.
