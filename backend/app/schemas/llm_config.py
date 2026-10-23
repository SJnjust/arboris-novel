# AIMETA P=LLM配置模式_模型配置请求响应|R=LLM配置结构|NR=不含业务逻辑|E=LLMConfigSchema|X=internal|A=Pydantic模式|D=pydantic|S=none|RD=./README.ai
from ..core.llm_options import reasoning_effort, optional_text
from typing import Optional

from pydantic import BaseModel, HttpUrl, Field, field_validator


class LLMConfigBase(BaseModel):
    llm_provider_url: Optional[HttpUrl] = Field(default=None, description="自定义 LLM 服务地址")
    llm_provider_api_key: Optional[str] = Field(default=None, description="自定义 LLM API Key")
    llm_provider_model: Optional[str] = Field(default=None, description="自定义模型名称")

    reasoning_effort: Optional[str] = None
    structured_model: Optional[str] = Field(default=None, max_length=255)
    structured_reasoning_effort: Optional[str] = None

    @field_validator("reasoning_effort", "structured_reasoning_effort", mode="before")
    @classmethod
    def normalize_effort(cls, value):
        return reasoning_effort(value)

    @field_validator("llm_provider_url", mode="before")
    @classmethod
    def normalize_url(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("structured_model", mode="before")
    @classmethod
    def normalize_model(cls, value):
        return optional_text(value)


class LLMConfigCreate(LLMConfigBase):
    pass


class LLMConfigRead(LLMConfigBase):
    user_id: int

    class Config:
        from_attributes = True


class ModelListRequest(BaseModel):
    llm_provider_url: Optional[str] = Field(default=None, description="LLM 服务地址")
    llm_provider_api_key: str = Field(..., description="LLM API Key")
