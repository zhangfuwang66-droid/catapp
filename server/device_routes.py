"""设备登记后的配对、归属与停用接口；公开入口不能创建设备凭证。"""

from async_helpers import run_blocking
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
    result = await run_blocking(request.app.state.identities.list_accessible_devices, user.id)
    for device in result:
        runtime = request.app.state.devices.devices.get(device["id"])
        device["audio_online"] = bool(device["control_allowed"] and runtime and runtime.user_id == user.id and runtime.esp32_audio_connected())
        device["camera_online"] = bool(device["control_allowed"] and runtime and runtime.user_id == user.id and runtime.esp32_camera_ws is not None)
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "devices": result}


@router.post("/pair")
async def pair(payload: PairRequest, request: Request, user: User = Depends(require_session_write)):
    try:
        device = await run_blocking(
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
        grant = await run_blocking(request.app.state.identities.request_pairing, device_id, bearer_token(request))
    except RateLimited as exc:
        raise rate_error(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    response.headers["Cache-Control"] = "no-store"
    # 固件使用剩余秒数倒计时，不依赖 HTTP 本地模式下尚未同步的设备时钟。
    expires_in = max(0, min(300, int(grant.expires_at - request.app.state.identities.clock())))
    return {"ok": True, "device_id": grant.device_id, "code": grant.code,
            "expires_at": grant.expires_at, "expires_in": expires_in}


@router.get("/{device_id}/binding")
async def binding(response: Response, device: dict = Depends(require_device_credential)):
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "device_id": device["id"], "bound": device["active_user_id"] is not None}


class ConfirmDevice(BaseModel):
    confirm_device_id: str = Field(min_length=1, max_length=64)


class LoanRequest(ConfirmDevice):
    hours: int = 4


async def transition(request, device_id, mutation, *args):
    registry = request.app.state.devices
    async with registry.locked(device_id):
        result = await run_blocking(mutation, device_id, *args)
        await request.app.state.connections.revoke_device(device_id, code=4403, reason="设备使用权已变更，请重新选择设备")
        await registry.retire_locked(device_id)
        return result


@router.post("/{device_id}/unbind")
async def unbind(device_id: str, payload: ConfirmDevice, request: Request, user: User = Depends(require_session_write)):
    if payload.confirm_device_id != device_id:
        raise HTTPException(400, "请核对要解绑的设备编号")
    try:
        await asyncio.shield(transition(request, device_id, request.app.state.identities.unbind_device, user.id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return {"ok":True, "message":"已解绑，保留 Wi-Fi 和历史记忆；设备联网后重新获取配对码"}


@router.post("/{device_id}/loan")
async def loan(device_id: str, payload: LoanRequest, request: Request, response: Response, user: User = Depends(require_session_write)):
    if payload.confirm_device_id != device_id:
        raise HTTPException(400, "请核对要出借的设备编号")
    try:
        result = await run_blocking(request.app.state.identities.create_loan, device_id, user.id, payload.hours)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.post("/borrow")
async def borrow(payload: PairRequest, request: Request, user: User = Depends(require_session_write)):
    try:
        device = await run_blocking(request.app.state.identities.claim_loan, payload.code, user.id,
                                   client_key=request.client.host if request.client else 'unknown')
        await run_blocking(request.app.state.memory_retention.ensure_policy,user.id,device['id'],'temporary')
        async with request.app.state.devices.locked(device['id']):
            await request.app.state.connections.revoke_device(device['id'], code=4403, reason="设备已进入临时借用")
            await request.app.state.devices.retire_locked(device['id'])
    except RateLimited as exc:
        raise rate_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'ok':True, 'device':device}


@router.post("/{device_id}/loan/end")
async def end_loan(device_id: str, payload: ConfirmDevice, request: Request, user: User = Depends(require_session_write)):
    if payload.confirm_device_id != device_id:
        raise HTTPException(400, "请核对设备编号")
    try:
        await asyncio.shield(transition(request, device_id, request.app.state.identities.end_loan, user.id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return {'ok':True}


@router.post("/{device_id}/revoke")
async def revoke(device_id: str, request: Request, user: User = Depends(require_session_write)):
    try:
        await run_blocking(request.app.state.identities.revoke_device, device_id, owner_user_id=user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="设备不存在或无权操作") from None
    await request.app.state.connections.revoke_device(device_id)
    await request.app.state.devices.retire(device_id)
    return {"ok": True}
