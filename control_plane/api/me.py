from fastapi import APIRouter, Depends

from control_plane.api.auth import AuthPrincipal, require_user_or_admin

router = APIRouter(tags=["identity"])


@router.get("/me")
def read_me(principal: AuthPrincipal = Depends(require_user_or_admin)):
    return {
        "subject": principal.subject,
        "role": principal.role,
        "projects": principal.projects,
        "expires_at": principal.expires_at,
    }
