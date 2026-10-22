# AIMETA P=LLM配置API_模型配置管理|R=LLM配置CRUD|NR=不含模型调用|E=route:GET_POST_/api/llm-config/*|X=http|A=配置CRUD|D=fastapi,sqlalchemy|S=db|RD=./README.ai
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

import asyncio
from ...core.request_deadline import RequestDeadlineRoute, request_deadline
from ...core.dependencies import get_current_user
from ...db.session import get_session
from ...schemas.llm_config import LLMConfigCreate, LLMConfigRead, ModelListRequest
from ...schemas.user import UserInDB
from ...services.llm_config_service import LLMConfigService


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm-config", tags=["LLM Configuration"], route_class=RequestDeadlineRoute)


def get_llm_config_service(session: AsyncSession = Depends(get_session)) -> LLMConfigService:
    return LLMConfigService(session)


@router.get("", response_model=LLMConfigRead)
async def read_llm_config(
    service: LLMConfigService = Depends(get_llm_config_service),
    current_user: UserInDB = Depends(get_current_user),
) -> LLMConfigRead:
    config = await service.get_config(current_user.id)
    if not config:
        logger.warning("用户 %s 尚未设置 LLM 配置", current_user.id)
        raise HTTPException(status_code=404, detail="尚未设置自定义配置")
    logger.info("用户 %s 获取 LLM 配置", current_user.id)
    return config


@router.put("", response_model=LLMConfigRead)
async def upsert_llm_config(
    payload: LLMConfigCreate,
    service: LLMConfigService = Depends(get_llm_config_service),
    current_user: UserInDB = Depends(get_current_user),
) -> LLMConfigRead:
    logger.info("用户 %s 更新 LLM 配置", current_user.id)
    return await service.upsert_config(current_user.id, payload)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_config(
    service: LLMConfigService = Depends(get_llm_config_service),
    current_user: UserInDB = Depends(get_current_user),
) -> None:
    deleted = await service.delete_config(current_user.id)
    if not deleted:
        logger.warning("用户 %s 删除 LLM 配置失败，未找到记录", current_user.id)
        raise HTTPException(status_code=404, detail="未找到配置")
    logger.info("用户 %s 删除 LLM 配置", current_user.id)


@router.post("/models", response_model=List[str])
@request_deadline
async def list_models(
    payload: ModelListRequest,
    service: LLMConfigService = Depends(get_llm_config_service),
    current_user: UserInDB = Depends(get_current_user),
) -> List[str]:
    """获取可用的模型列表"""
    try:
        async with asyncio.timeout(35):
            models = await service.get_available_models(
                api_key=payload.llm_provider_api_key,
                base_url=payload.llm_provider_url
            )
        logger.info("用户 %s 获取模型列表，返回 %d 个模型", current_user.id, len(models))
        return models
    except Exception as e:
        logger.error("用户 %s 获取模型列表失败: %s", current_user.id, str(e))
        # 返回空列表而不是抛出异常，因为这只是提示功能
        return []
