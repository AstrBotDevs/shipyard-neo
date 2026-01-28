#!/usr/bin/env python3

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8123, reload=False, log_level="info"
    )
