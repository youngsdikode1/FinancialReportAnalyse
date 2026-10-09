from typing import List, Optional
from pydantic import BaseModel


class CRAGBatchIngestion(BaseModel):
    """单份财报批量入库请求，一份财报对应一个独立collection"""
    m_collection_name: str          # 单份财报专属collection名称
    m_content: str                  # 财报完整未分割原始文本(raw_full_text)
    m_over_write: bool = True       # True：入库前清空本collection全部旧数据；False：追加写入
    m_chunk_size: int               # 分块大小(token)
    m_over_lap_size: int            # 块重叠大小(token)


class CRAGRetrieveRequest(BaseModel):
    """单份财报召回请求，只查询该财报专属collection"""
    m_collection_name: str
    m_query: str                    # RAG查询：自然语言问句
    m_top_k: int                    # 返回top‑k结果数量
    m_score_threshold: float        # 相似度阈值，低于该分数直接过滤丢弃


class CRAGRetrieveItem(BaseModel):
    """单条命中chunk子项，m_chunks列表的元素"""
    m_content: str
    m_score: float
    m_metadata: dict
    
    def GetIndex(self) -> Optional[int]:
        """
        获取本chunk在collection内的分块索引 chunk_idx
        从 m_metadata["chunk_idx"] 读取；元数据不存在返回None
        """
        return self.m_metadata.get("chunk_idx")

class CRAGRetrieveResult(BaseModel):
    """召回返回结果"""
    m_collection_name: str
    m_query: str
    m_chunks: List[CRAGRetrieveItem] = []