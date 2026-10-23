# AIMETA P=LLM配置服务_模型配置业务逻辑|R=配置管理_模型选择|NR=不含模型调用|E=LLMConfigService|X=internal|A=服务类|D=sqlalchemy|S=db|RD=./README.ai
from typing import Optional, List
import logging
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI

from ..models import LLMConfig
from ..repositories.llm_config_repository import LLMConfigRepository
from ..repositories.system_config_repository import SystemConfigRepository
from ..schemas.llm_config import LLMConfigCreate, LLMConfigRead


logger = logging.getLogger(__name__)


class LLMConfigService:
    """用户自定义 LLM 配置服务。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = LLMConfigRepository(session)
        self.system_config_repo = SystemConfigRepository(session)

    def _identify_provider(self, base_url: Optional[str]) -> str:
        """根据 base_url 识别 LLM 提供商"""
        if not base_url:
            return "openai"

        url_lower = base_url.lower()
        parsed = urlparse(url_lower)
        host = parsed.netloc or parsed.path

        # 识别常见提供商
        if "openai.com" in host or "api.openai.com" in host:
            return "openai"
        elif "anthropic.com" in host or "api.anthropic.com" in host:
            return "anthropic"
        elif "generativelanguage.googleapis.com" in host or "google" in host:
            return "google"
        elif "azure" in host:
            return "azure"
        elif "cohere" in host:
            return "cohere"
        elif "together" in host or "together.ai" in host:
            return "together"
        elif "deepseek" in host:
            return "deepseek"
        elif "moonshot" in host:
            return "moonshot"
        elif "zhipu" in host or "bigmodel.cn" in host:
            return "zhipu"
        elif "baidu" in host or "qianfan" in host:
            return "baidu"
        else:
            # 默认使用 OpenAI-like API
            return "openai-like"

    def _build_url(self, base_url: Optional[str], default_url: str, path_suffix: str) -> str:
        """统一的 URL 构建逻辑，避免路径重复"""
        if base_url:
            url = base_url.rstrip('/')
            # 如果 URL 已经包含路径后缀，则直接使用
            if not url.endswith(path_suffix):
                url += path_suffix
        else:
            url = default_url
        return url

    async def upsert_config(self, user_id: int, payload: LLMConfigCreate) -> LLMConfigRead:
        instance = await self.repo.get_by_user(user_id)
        data = payload.model_dump(exclude_unset=True)
        if "llm_provider_url" in data and data["llm_provider_url"] is not None:
            # HttpUrl 类型在 sqlite 中无法直接写入，需要提前转为字符串
            data["llm_provider_url"] = str(data["llm_provider_url"])
        if instance:
            # 显式的空值表示清除个人覆盖，不走忽略 None 的通用仓库更新。
            for name, value in data.items():
                setattr(instance, name, value)
            await self.session.flush()
        else:
            instance = LLMConfig(user_id=user_id, **data)
            await self.repo.add(instance)
        await self.session.commit()
        return LLMConfigRead.model_validate(instance)

    async def get_config(self, user_id: int) -> Optional[LLMConfigRead]:
        instance = await self.repo.get_by_user(user_id)
        return LLMConfigRead.model_validate(instance) if instance else None

    async def delete_config(self, user_id: int) -> bool:
        instance = await self.repo.get_by_user(user_id)
        if not instance:
            return False
        await self.repo.delete(instance)
        await self.session.commit()
        return True

    async def get_available_models(
        self, api_key: str, base_url: Optional[str] = None
    ) -> List[str]:
        """使用指定的凭证获取可用的模型列表"""
        if not api_key:
            logger.warning("获取模型列表失败：未提供 API Key")
            return []

        # 识别提供商
        provider = self._identify_provider(base_url)
        logger.info("识别到 LLM 提供商: %s (base_url: %s)", provider, base_url)

        try:
            # 根据不同提供商获取模型列表
            if provider == "anthropic":
                return await self._get_anthropic_models(api_key, base_url)
            elif provider == "google":
                return await self._get_google_models(api_key, base_url)
            elif provider == "azure":
                return await self._get_azure_models(api_key, base_url)
            elif provider == "cohere":
                return await self._get_cohere_models(api_key, base_url)
            else:
                # OpenAI 和 OpenAI-like (包括 together, deepseek, moonshot, zhipu 等)
                return await self._get_openai_like_models(api_key, base_url)
        except Exception as e:
            error_msg = str(e)
            logger.error("获取模型列表失败: provider=%s, error=%s", provider, error_msg, exc_info=True)

            # 提供更友好的错误信息
            if "Connection error" in error_msg or "disconnected" in error_msg.lower():
                logger.warning("连接错误，可能是 API URL 配置错误或网络问题")
            elif "401" in error_msg or "Unauthorized" in error_msg:
                logger.warning("认证失败，请检查 API Key 是否正确")
            elif "404" in error_msg or "Not Found" in error_msg:
                logger.warning("API 端点不存在，请检查 URL 是否正确")

            return []

    async def _get_openai_like_models(self, api_key: str, base_url: Optional[str]) -> List[str]:
        """获取 OpenAI 或 OpenAI-like API 的模型列表"""
        import httpx
        from openai import APIConnectionError, APIError

        try:
            # 创建带有超时和重试配置的客户端
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=httpx.Timeout(30.0, connect=10.0),
                max_retries=2,
            )

            logger.info("尝试获取模型列表: base_url=%s", base_url)
            models_response = await client.models.list()
            model_ids = [model.id for model in models_response.data]
            logger.info("成功获取 %d 个 OpenAI-like 模型", len(model_ids))
            return sorted(model_ids)

        except APIConnectionError as e:
            logger.error("API 连接错误: %s", str(e), exc_info=True)
            # 某些自建服务可能不支持 /v1/models 端点，尝试使用 httpx 直接请求
            return await self._get_models_via_http(api_key, base_url)

        except APIError as e:
            logger.error("API 调用错误: status_code=%s, message=%s", getattr(e, 'status_code', 'unknown'), str(e))
            return await self._get_models_via_http(api_key, base_url)

        except Exception as e:
            logger.error("获取 OpenAI-like 模型列表失败: %s", str(e), exc_info=True)
            return await self._get_models_via_http(api_key, base_url)

    async def _get_models_via_http(self, api_key: str, base_url: Optional[str]) -> List[str]:
        """使用 httpx 直接请求模型列表（备选方案）"""
        import httpx

        try:
            # 构建完整的 URL
            if base_url:
                url = base_url.rstrip('/') + '/models'
            else:
                url = 'https://api.openai.com/v1/models'

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            logger.info("使用 HTTP 直接请求模型列表: url=%s", url)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)

                logger.info("HTTP 响应状态码: %d", response.status_code)

                if response.status_code == 200:
                    data = response.json()
                    models = data.get('data', [])
                    model_ids = [model.get('id') for model in models if model.get('id')]
                    logger.info("通过 HTTP 成功获取 %d 个模型", len(model_ids))
                    return sorted(model_ids)
                elif response.status_code == 404:
                    logger.warning("模型列表端点不存在 (404)，该服务可能不支持模型列表查询")
                    return []
                elif response.status_code == 401:
                    logger.warning("认证失败 (401)，请检查 API Key 是否正确")
                    return []
                else:
                    logger.warning("HTTP 请求失败: status=%d, body=%s", response.status_code, response.text[:200])
                    return []

        except httpx.TimeoutException:
            logger.error("HTTP 请求超时")
            return []
        except httpx.ConnectError as e:
            logger.error("无法连接到服务器: %s", str(e))
            return []
        except Exception as e:
            logger.error("HTTP 请求失败: %s", str(e), exc_info=True)
            return []

    async def _get_anthropic_models(self, api_key: str, base_url: Optional[str]) -> List[str]:
        """获取 Anthropic 的模型列表"""
        # Anthropic 目前不提供模型列表 API，返回常用模型
        logger.info("返回 Anthropic 预定义模型列表")
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]

    async def _get_google_models(self, api_key: str, base_url: Optional[str]) -> List[str]:
        """获取 Google Gemini 的模型列表"""
        import httpx

        try:
            # 使用统一的 URL 构建方法
            url = self._build_url(
                base_url,
                "https://generativelanguage.googleapis.com/v1beta",
                "/v1beta"
            )
            url += f"/models?key={api_key}"

            logger.info("请求 Google 模型列表: url=%s", url.replace(api_key, "***"))

            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)

                logger.info("HTTP 响应状态码: %d", response.status_code)
                response.raise_for_status()
                data = response.json()

                model_ids = []
                for model in data.get("models", []):
                    model_name = model.get("name", "")
                    # 移除 "models/" 前缀
                    if model_name.startswith("models/"):
                        model_name = model_name[7:]
                    # 只返回生成模型（非 embedding 模型）
                    if "generateContent" in model.get("supportedGenerationMethods", []):
                        model_ids.append(model_name)

                logger.info("成功获取 %d 个 Google 模型", len(model_ids))
                return sorted(model_ids)
        except httpx.HTTPStatusError as e:
            logger.error("Google API HTTP 错误: status=%d, message=%s", e.response.status_code, str(e))
            # 返回常用的 Gemini 模型作为备选
            return [
                "gemini-2.0-flash-exp",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-1.0-pro",
            ]
        except httpx.TimeoutException:
            logger.error("Google API 请求超时")
            return [
                "gemini-2.0-flash-exp",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-1.0-pro",
            ]
        except Exception as e:
            logger.error("获取 Google 模型列表失败: %s", str(e), exc_info=True)
            # 返回常用的 Gemini 模型作为备选
            return [
                "gemini-2.0-flash-exp",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-1.0-pro",
            ]

    async def _get_azure_models(self, api_key: str, base_url: Optional[str]) -> List[str]:
        """获取 Azure OpenAI 的模型列表"""
        # Azure OpenAI 的部署是用户自定义的，无法直接列举
        # 返回常见的 Azure OpenAI 模型名称
        logger.info("返回 Azure OpenAI 预定义模型列表")
        return [
            "gpt-4",
            "gpt-4-32k",
            "gpt-4-turbo",
            "gpt-4o",
            "gpt-35-turbo",
            "gpt-35-turbo-16k",
        ]

    async def _get_cohere_models(self, api_key: str, base_url: Optional[str]) -> List[str]:
        """获取 Cohere 的模型列表"""
        import httpx

        try:
            # 使用统一的 URL 构建方法
            url = self._build_url(
                base_url,
                "https://api.cohere.ai/v1",
                "/v1"
            )
            url += "/models"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            logger.info("请求 Cohere 模型列表: url=%s", url)

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=30.0)

                logger.info("HTTP 响应状态码: %d", response.status_code)
                response.raise_for_status()
                data = response.json()

                model_ids = [model.get("name") for model in data.get("models", []) if model.get("name")]
                logger.info("成功获取 %d 个 Cohere 模型", len(model_ids))
                return sorted(model_ids)
        except httpx.HTTPStatusError as e:
            logger.error("Cohere API HTTP 错误: status=%d, message=%s", e.response.status_code, str(e))
            return [
                "command-r-plus",
                "command-r",
                "command",
                "command-light",
            ]
        except httpx.TimeoutException:
            logger.error("Cohere API 请求超时")
            return [
                "command-r-plus",
                "command-r",
                "command",
                "command-light",
            ]
        except Exception as e:
            logger.error("获取 Cohere 模型列表失败: %s", str(e), exc_info=True)
            # 返回常用的 Cohere 模型作为备选
            return [
                "command-r-plus",
                "command-r",
                "command",
                "command-light",
            ]
