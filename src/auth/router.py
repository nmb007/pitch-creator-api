"""Auth API routes."""

from asyncer import asyncify
from fastapi import APIRouter, HTTPException, status

from src.auth import service as auth_service
from src.auth.dependencies import CurrentUserDep
from src.auth.exceptions import InvalidCredentials
from src.auth.schemas import LoginRequest, TokenResponse, UserResponse
from src.schemas import error_response, success_response

router = APIRouter(prefix="/auth", tags=["auth"])

_TOKEN_EXAMPLE = {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
}


@router.post(
    "/login",
    summary="Log in",
    description="Exchange username and password for a JWT access token.",
    responses={
        status.HTTP_200_OK: success_response(
            model=TokenResponse,
            description="Access token issued",
            example=_TOKEN_EXAMPLE,
        ),
        status.HTTP_401_UNAUTHORIZED: error_response(
            description="Invalid credentials",
            detail="Invalid credentials",
        ),
    },
)
async def login(payload: LoginRequest) -> TokenResponse:
    try:
        token = await asyncify(auth_service.authenticate)(payload.username, payload.password)
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return TokenResponse(access_token=token)


@router.get("/me", summary="Current user")
async def me(user: CurrentUserDep) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        name=user.name,
        created_at=user.created_at,
    )
