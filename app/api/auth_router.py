from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import LoginRequest, LoginResponse
from app.auth import AdminAuth

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Exchange the admin credentials for a signed bearer token."""
    auth: AdminAuth = request.app.state.auth
    if not auth.enabled:
        raise HTTPException(
            status_code=501,
            detail=(
                "Authentication is disabled on this server "
                "(ADMIN_USERNAME/ADMIN_PASSWORD unset)."
            ),
        )
    if not auth.check_credentials(body.username, body.password):
        # No WWW-Authenticate realm header here: the SPA reserves that marker
        # for expired-session 401s, and a failed login must not trigger it.
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return LoginResponse(
        token=auth.make_token(),
        expires_in=request.app.state.settings.admin_session_ttl_seconds,
    )
