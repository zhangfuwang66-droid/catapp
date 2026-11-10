from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel,Field,ConfigDict
from access_control import require_session,require_session_write
from identity_store import User
from async_helpers import run_blocking,finish_before_cancel
from provider_routes import lock_for,retire_user_models

router = APIRouter(prefix='/api/account/speech')


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: str = Field(default='fast',pattern='^(fast|accurate)$')
    language: str = Field(default='auto',pattern='^(auto|zh|yue|en)$')
    hotwords: list[str] = Field(default_factory=list,max_length=64)
    corrections: dict[str,str] = Field(default_factory=dict,max_length=32)


@router.get('')
async def get(request:Request,response:Response,user:User=Depends(require_session)):
    response.headers['Cache-Control']='no-store'
    return await run_blocking(request.app.state.speech.settings.get,user.id)


@router.put('')
async def save(body:SpeechRequest,request:Request,response:Response,user:User=Depends(require_session_write)):
    response.headers['Cache-Control']='no-store'
    async def perform():
        settings = await run_blocking(request.app.state.speech.settings.save,user.id,body.model_dump())
        await retire_user_models(request.app,user.id)
        return settings
    try:
        async with lock_for(request.app,user.id):
            await require_session_write(request)
            return await finish_before_cancel(perform())
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
