# Ionworks API Client

> **This is a read-only mirror.** The source of truth is a private repo.

A Python client for interacting with the [Ionworks](https://ionworks.com) API.

## Installation

```bash
pip install ionworks-api
```

## Quick start

```python
from ionworks import Ionworks

# Initialize client (uses IONWORKS_API_KEY from environment/.env file)
client = Ionworks()

# or provide credentials directly
client = Ionworks(api_key="your_key")
```

Get your API key from the [Ionworks account settings](https://app.ionworks.com/dashboard/account).

## Sub-clients

The client exposes domain-specific sub-clients:

| Sub-client | Access | Description |
|---|---|---|
| Projects | `client.project` | Create, list, update, delete projects |
| Models | `client.model` | Create, list, update, delete models |
| Parameterized models | `client.parameterized_model` | List, create, get parameter values |
| Studies | `client.study` | Manage studies and assign simulations/measurements |
| Protocols | `client.protocol` | Validate UCP protocols |
| Simulations | `client.simulation` | Run simulations and retrieve results |
| Pipelines | `client.pipeline` | Submit parameterization pipelines |
| Optimizations | `client.optimization` | Run design optimizations |
| Cell specifications | `client.cell_spec` | Manage cell specifications |
| Cell instances | `client.cell_instance` | Manage cell instances |
| Cell measurements | `client.cell_measurement` | Upload and retrieve measurement data |
| Jobs | `client.job` | Monitor and cancel background jobs |

## Documentation

- **Guides and tutorials**: [docs.ionworks.com](https://docs.ionworks.com/api-client)
- **API reference**: [api.docs.ionworks.com](https://api.docs.ionworks.com)
- **Changelog**: [`CHANGELOG.md`](./CHANGELOG.md) for this package; [docs.ionworks.com/changelog](https://docs.ionworks.com/changelog) for the full Ionworks platform changelog.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `IONWORKS_API_KEY` | Yes | — | API key from [account settings](https://app.ionworks.com/dashboard/account) |
| `IONWORKS_API_URL` | No | `https://api.ionworks.com` | API base URL |
| `PROJECT_ID` | For pipelines | — | Project ID from your project settings page |

The client loads `.env` automatically via `python-dotenv`.
