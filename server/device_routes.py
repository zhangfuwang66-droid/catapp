"""设备登记后的配对、归属与停用接口；公开入口不能创建设备凭证。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from access_control import bearer_token, require_device_credential, require_session, require_session_write
from identity_store import RateLimited, User

router = APIRouter(prefix="/api/devices")


class PairRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32, repr=False)


def rate_error(exc: RateLimited):
    return HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)})


@router.get("")
async def devices(request: Request, response: Response, user: User = Depends(require_session)):
    result = await asyncio.to_thread(request.app.state.identities.list_devices, user.id)
    for device in result:
        runtime = request.app.state.devices.devices.get(device["id"])
        device["audio_online"] = bool(runtime and runtime.esp32_audio_connected())
        device["camera_online"] = bool(runtime and runtime.esp32_camera_ws is not None)
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "devices": result}


@router.post("/pair")
async def pair(payload: PairRequest, request: Request, user: User = Depends(require_session_write)):
    try:
        device = await asyncio.to_thread(
            request.app.state.identities.claim_pairing, payload.code, user.id,
            client_key=request.client.host if request.client else "unknown",
        )
    except RateLimited as exc:
        raise rate_error(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "device": device}


@router.post("/{device_id}/pairing")
async def pairing(device_id: str, request: Request, response: Response, device: dict = Depends(require_device_credential)):
    try:
        grant = await asyncio.to_thread(request.app.state.identities.request_pairing, device_id, bearer_token(request))
    except RateLimited as exc:
        raise rate_error(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "device_id": grant.device_id, "code": grant.code, "expires_at": grant.expires_at}


@router.get("/{device_id}/binding")
async def binding(response: Response, device: dict = Depends(require_device_credential)):
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "device_id": device["id"], "bound": device["owner_user_id"] is not None}


@router.post("/{device_id}/revoke")
async def revoke(device_id: str, request: Request, user: User = Depends(require_session_write)):
    try:
        await asyncio.to_thread(request.app.state.identities.revoke_device, device_id, owner_user_id=user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="设备不存在或无权操作") from None
    await request.app.state.connections.revoke_device(device_id)
    await request.app.state.devices.retire(device_id)
    return {"ok": True}
