"""
CORS configuration for the BW Interop demonstration frontend.
"""

from fastapi.middleware.cors import CORSMiddleware


def enable_frontend_cors(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
        ],
        allow_credentials=False,
        allow_methods=[
            "GET",
            "POST",
            "OPTIONS",
        ],
        allow_headers=[
            "Content-Type",
            "Authorization",
        ],
    )

    return app
