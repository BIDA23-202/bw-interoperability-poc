"""CORS helper for the static BW Interop frontend."""
from fastapi.middleware.cors import CORSMiddleware


def enable_frontend_cors(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5500",
            "http://localhost:5500",
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "https://bida23-202.github.io",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    return app
