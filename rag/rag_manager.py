from typing import List
import os
from qdrant_client import QdrantClient, models
from langchain_text_splitters import TokenTextSplitter
from sentence_transformers import SentenceTransformer
from rag.rag_request import CRAGBatchIngestion, CRAGRetrieveRequest, CRAGRetrieveResult, CRAGRetrieveItem
from utils.logs import LogInfo
from utils.config import CConfigManager

DIMS = {
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-large-zh-v1.5": 1024,
}


class CRAGManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        """单例控制，只创建一个实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # 防止多次 __init__ 重复初始化
        if hasattr(self, "_initialized") and self._initialized:
            return

        cfg = CConfigManager.GetInstance()
        self._qdrant_url = cfg.GetValue("qdrant_url", "http://127.0.0.1:6333")

        vec_model = "BAAI/bge-base-zh-v1.5"
        self._embedding_model_name = cfg.GetValue("embedding_model", vec_model)

        # 关闭版本兼容性校验，消除client1.19 / server1.17警告
        self.client = QdrantClient(url=self._qdrant_url, check_compatibility=False)

        LogInfo(f"CRAGManager loading embedding model: {self._embedding_model_name}")
        self._embedding_model = SentenceTransformer(
            self._embedding_model_name,
            local_files_only=True,
            #device="cpu"
        )
        LogInfo(f"CRAGManager SentenceTransformer")

        self._embedding_dim = self.__GetDIM()
        LogInfo(f"embedding dim = {self._embedding_dim}")

        self.BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
        self._initialized = True

    def __GetDIM(self):
        ret_dim = None
        try:
            ret_dim = self._embedding_model.get_embedding_dimension()
        except Exception:
            pass
        if ret_dim:
            return ret_dim
        # 使用实际加载的模型名称查表，修复原bug
        return DIMS.get(self._embedding_model_name)

    @classmethod
    def GetInstance(cls) -> "CRAGManager":
        """获取单例实例，业务代码统一调用这个方法拿对象"""
        return cls()

    def CheckQdrant(self) -> bool:
        """检查Qdrant服务是否正常连通"""
        try:
            # 轻量探测，校验网络连通性
            _ = self.client.collection_exists(collection_name="____test_none_123456")
            LogInfo("CRAGManager.CheckQdrant ok")
            return True
        except Exception as e:
            LogInfo(f"CRAGManager.CheckQdrant failed: {str(e)}")
            return False

    def BatchIngestion(self, req: CRAGBatchIngestion) -> bool:
        """
        单份财报批量入库
        一份财报对应一个collection
        :param req: CRAGBatchIngestion 请求对象
        :return: bool 成功/失败
        """
        coll_name = req.m_collection_name
        LogInfo(f"CRAGManager.BatchIngestion start collection={coll_name}, over_write={req.m_over_write}")

        if req.m_over_write:
            if self.client.collection_exists(collection_name=coll_name):
                self.client.delete_collection(collection_name=coll_name)
                LogInfo(f"CRAGManager.BatchIngestion drop old collection: {coll_name}")

        splitter = TokenTextSplitter(
            chunk_size=req.m_chunk_size,
            chunk_overlap=req.m_over_lap_size,
            encoding_name="cl100k_base"
        )
        chunk_texts = splitter.split_text(req.m_content)

        if len(chunk_texts) == 0:
            LogInfo("CRAGManager.BatchIngestion chunks empty, skip ingest")
            return False

        if not self.client.collection_exists(collection_name=coll_name):
            self.client.create_collection(
                collection_name=coll_name,
                vectors_config=models.VectorParams(
                    size=self._embedding_dim,
                    distance=models.Distance.COSINE
                )
            )

        total_chunk_count = len(chunk_texts)
        LogInfo(f"CRAGManager.BatchIngestion start embedding chunks count={total_chunk_count}")

        # 指定batch_size优化CPU推理速度，云服务器CPU建议8‑16，内存紧张就下调
        chunk_vectors = self._embedding_model.encode(
            chunk_texts,
            convert_to_numpy=False,
            batch_size=12
        )
        LogInfo(f"CRAGManager.BatchIngestion encode done, len={len(chunk_texts)}")

        points: List[models.PointStruct] = []
        for idx, (chunk_text, vec) in enumerate(zip(chunk_texts, chunk_vectors)):
            meta = {
                "start_index": idx,
                "chunk_idx": idx
            }
            points.append(models.PointStruct(
                id=idx,
                vector=vec,
                payload={
                    "content": chunk_text,
                    "metadata": meta
                }
            ))
            # 每10条打印进度
            current = idx + 1
            if current % 10 == 0:
                remain = total_chunk_count - current
                LogInfo(f"CRAGManager.BatchIngestion progress: inserted {current}, remaining {remain}")

        # wait=True 等待磁盘落盘，防止脚本直接退出丢数据
        self.client.upsert(collection_name=coll_name, points=points, wait=True)
        LogInfo(f"CRAGManager.BatchIngestion finish, chunk_count={total_chunk_count}")
        return True

    def Retrieve(self, req: CRAGRetrieveRequest) -> CRAGRetrieveResult:
        """
        召回查询，只操作指定collection
        【无try‑except，异常向上抛出】
        对齐本机可运行demo：query_points + vector.tolist()
        :param req: CRAGRetrieveRequest
        :return: CRAGRetrieveResult
        """
        coll_name = req.m_collection_name
        result = CRAGRetrieveResult(
            m_collection_name=coll_name,
            m_query=req.m_query,
            m_chunks=[]
        )

        if not self.client.collection_exists(collection_name=coll_name):
            LogInfo(f"CRAGManager.Retrieve collection not exists:{coll_name}")
            return result

        query_text = self.BGE_QUERY_PREFIX + req.m_query
        query_vector = self._embedding_model.encode(query_text, convert_to_numpy=False)

        resp = self.client.query_points(
            collection_name=coll_name,
            query=query_vector.tolist(),
            limit=req.m_top_k,
            score_threshold=req.m_score_threshold,
            with_payload=True
        )
        hits = resp.points
        LogInfo(f"CRAGManager.Retrieve query=[{req.m_query}], hit count={len(hits)}")

        for hit in hits:
            item = CRAGRetrieveItem(
                m_content=hit.payload.get("content", ""),
                m_metadata=hit.payload.get("metadata", {}),
                m_score=hit.score
            )
            result.m_chunks.append(item)
        return result


    def GetCollectionInfos(self) -> List[dict]:
        """
        遍历 Qdrant 全部 collection，返回列表：
            [{"name": ..., "count": ..., "status": ...}, ...]
        - count 使用近似统计（exact=False），大 collection 也快
        - status 来自 Qdrant 自身健康状态：green / yellow / grey / red
        - 过滤 ____ 开头的内部 collection
        - 整体异常返回空列表；单个 collection 异常给 count=None, status=None
        - 结果按 name 升序排序
        供前端管理页面调用。
        """
        result: List[dict] = []
        try:
            collections_resp = self.client.get_collections()
            collections = collections_resp.collections
        except Exception as e:
            LogStack(e, "CRAGManager.GetCollectionInfos get_collections fail")
            return result

        for coll in collections:
            name = coll.name
            if name.startswith("____"):
                continue
            try:
                info = self.client.get_collection(collection_name=name)
                cnt = self.client.count(collection_name=name, exact=False)
                result.append({
                    "name": name,
                    "count": cnt.count,
                    "status": str(info.status),
                })
            except Exception as e:
                LogStack(e, f"CRAGManager.GetCollectionInfos fail name={name}")
                result.append({"name": name, "count": None, "status": None})

        result.sort(key=lambda x: x["name"])
        return result


# ===================== 测试用例 拆分入库、召回两个独立函数 =====================
def TestRagIngestOnly():
    """测试：仅执行读取maotai‑2024.txt，批量入库"""
    print("==== TestRagIngestOnly start ====")
    txt_file_path = "./maotai-2024.txt"
    if not os.path.exists(txt_file_path):
        print(f"测试失败：文件不存在 {txt_file_path}，请把maotai‑2024.txt放到项目运行目录")
        return False
    with open(txt_file_path, "r", encoding="utf-8") as f:
        file_content = f.read()
    rag_mgr = CRAGManager.GetInstance()
    ok = rag_mgr.CheckQdrant()
    if not ok:
        print("Qdrant服务连接失败，终止入库测试")
        return False
    test_coll_name = "test_maotai_2024"
    ingest_req = CRAGBatchIngestion(
        m_collection_name=test_coll_name,
        m_content=file_content,
        m_over_write=True,
        m_chunk_size=1500,
        m_over_lap_size=250
    )
    ingest_ok = rag_mgr.BatchIngestion(ingest_req)
    print(f"批量入库结果：{ingest_ok}")
    print("==== TestRagIngestOnly done ====")
    return ingest_ok


def TestRagRetrieveOnly():
    """测试：仅执行召回查询（依赖test_maotai_2024集合已经提前入库）"""
    print("\n==== TestRagRetrieveOnly start ====")
    rag_mgr = CRAGManager.GetInstance()
    ok = rag_mgr.CheckQdrant()
    if not ok:
        print("Qdrant服务连接失败，终止召回测试")
        return
    test_coll_name = "test_maotai_2024"
    ret_req = CRAGRetrieveRequest(
        m_collection_name=test_coll_name,
        m_query="2024年归母净利润是多少",
        m_top_k=4,
        m_score_threshold=0.0
    )
    ret_result = rag_mgr.Retrieve(ret_req)
    print(f"查询query：{ret_result.m_query}，命中块数量：{len(ret_result.m_chunks)}")
    for idx, item in enumerate(ret_result.m_chunks):
        print(f"\n---------- chunk {idx+1} score={item.m_score:.4f} ----------")
        print(f"meta: {item.m_metadata}")
        print(f"content:\n{item.m_content[:600]}...")
    print("==== TestRagRetrieveOnly done ====")


if __name__ == "__main__":
    # 按需注释切换
    TestRagIngestOnly()
    print("--------------------------------")
    TestRagRetrieveOnly()