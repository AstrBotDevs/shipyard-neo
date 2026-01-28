# Shipyard Python SDK

A Python SDK for interacting with Shipyard containerized execution environments.

## Installation

```bash
pip install aiohttp
```

## Quick Start

```python
import asyncio
from shipyard_python_sdk import ShipyardClient, Spec, create_session_ship

async def main():
    # Option 1: Using client directly
    client = ShipyardClient(
        endpoint_url="http://localhost:8156",
        access_token="your-token"
    )
    
    ship = await client.create_ship(
        ttl=3600,  # 1 hour
        spec=Spec(cpus=1.0, memory="512m"),
        max_session_num=5
    )
    
    # Use the ship
    await ship.fs.create_file("hello.txt", "Hello, World!")
    result = await ship.fs.read_file("hello.txt")
    print(result["content"])
    
    # Option 2: Using convenience function
    ship = await create_session_ship(
        ttl=1800,
        spec=Spec(cpus=0.5, memory="256m")
    )
    
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## File Structure

```
shipyard_python_sdk/
├── __init__.py          # Main package exports
├── types.py             # Type definitions and data models
├── client.py            # Main ShipyardClient implementation
├── session.py           # SessionShip implementation
├── filesystem.py        # File system operations component
├── shell.py             # Shell operations component
├── python.py            # Python/IPython operations component
├── utils.py             # Convenience functions
└── examples.py          # Usage examples
```

## Components

### ShipyardClient
Main client class for interacting with the Bay API.

### SessionShip
Represents a ship session with three main components:
- `ship.fs` - File system operations
- `ship.shell` - Shell command execution  
- `ship.python` - Python code execution

### Spec
Resource specification for ships:
```python
spec = Spec(cpus=2.0, memory="1g")
```

## Environment Variables

- `SHIPYARD_ENDPOINT` - Bay API endpoint URL
- `SHIPYARD_TOKEN` - Access token for authentication

## Error Handling

All operations can raise exceptions. Wrap calls in try-catch blocks:

```python
try:
    result = await ship.fs.read_file("nonexistent.txt")
except Exception as e:
    print(f"Error: {e}")
```

## Examples

See `examples.py` for comprehensive usage examples including:
- Basic file operations
- Shell command execution
- Python code execution with persistent variables
- Background processes
- Error handling patterns
