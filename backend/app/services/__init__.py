# AIMETA P=服务包初始化_导出所有服务类|R=包标识|NR=不含服务实现|E=-|X=internal|A=-|D=none|S=none|RD=./README.ai
"""
服务层包初始化

导出所有服务类，包括：
- 基础服务：LLM、向量存储、嵌入等
- 融合服务：一致性检查、蓝图管理
"""

# 基础服务
from .llm_service import LLMService
from .vector_store_service import VectorStoreService, RetrievedChunk, RetrievedSummary
from .embedding_service import EmbeddingService

# 融合服务（来自 AI_NovelGenerator 的设计理念）
from .consistency_service import ConsistencyService, ConsistencyCheckResult, ConsistencyViolation, ViolationSeverity
from .blueprint_service import BlueprintService
from .vector_store_service_ext import VectorStoreServiceExt

__all__ = [
    # 基础服务
    "LLMService",
    "VectorStoreService",
    "VectorStoreServiceExt",
    "EmbeddingService",
    "RetrievedChunk",
    "RetrievedSummary",
    # 融合服务
    "ConsistencyService",
    "ConsistencyCheckResult",
    "ConsistencyViolation",
    "ViolationSeverity",
    "BlueprintService",
]
