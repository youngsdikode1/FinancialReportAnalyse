import os
import json
from typing import Optional, Dict, List
from financials.report_date import CReportDate, TYPE_ANNUAL, TYPE_HALF, TYPE_QUARTER
from utils.config import CConfigManager
from rag.rag_manager import CRAGManager
from rag.rag_request import CRAGBatchIngestion, CRAGRetrieveRequest
import re
from utils.logs import PrintLog, LogInfo
# 路径宏定义，全局统一，不再函数传参
BASE_COMPANY_DATA_DIR = "./datas/company"


class CReportRAGChunk:
    """
    RAG召回单块结果，业务对外实体
    【注意】__eq__、__hash__全部基于m_index；仅在**同一个collection（同一份财报）**内才有比较意义。
    正常业务链路 m_index 为int；仅元数据损坏/旧集合才会出现m_index=None故障场景。
    """
    def __init__(self):
        self.m_content: str = ""
        self.m_page_nums: List[int] = []   # 来源页码列表，后续pdf解析补齐，当前默认为空
        self.m_index: Optional[int] = None

    def SetIndex(self, idx: Optional[int]):
        self.m_index = idx

    def __eq__(self, other):
        """仅依据 m_index 判断相等，仅限同一份财报collection内有效"""
        if not isinstance(other, CReportRAGChunk):
            return False
        return self.m_index == other.m_index

    def __hash__(self):
        """与__eq__配套，哈希值使用m_index；故障场景m_index=None时hash(None)生效"""
        return hash(self.m_index)

    def CalPageNum(self) -> None:
        """
        从m_content文本分割线标记解析页码
        标记样例： ------------ grewtrewsfdg--页码号 6 页码号--grewtrewsfdg ---------------
        规则：
        1. 提取所有识别到页码，去重排序
        2. 如果最小页码>1，自动追加【最小页码‑1】，补偿被切分丢失的上页内容
        3. 最终再次去重、升序存入 self.m_page_nums；无匹配则为空列表
        """
        self.m_page_nums.clear()
        # 正则捕获： --页码号 数字 页码号--
        pattern = re.compile(r"--页码号\s+(\d+)\s+页码号--")
        matches = pattern.findall(self.m_content)
        if not matches:
            return
        page_set = {int(pg) for pg in matches}
        min_p = min(page_set)
        # 最小页码大于1，补充上一页
        if min_p > 1:
            page_set.add(min_p - 1)
        self.m_page_nums = sorted(page_set)

    #发给LLM带内容分割线的chunk内容数据
    def GetLLMContent(self):
        idx_text = str(self.m_index) if self.m_index is not None else "?"
        ret_string = f"\n----这是{idx_text}号RAG chunk的内容分割线----\n"
        if self.m_page_nums:
            ret_string += f"这个chunk在财报中的页码来源:{self.m_page_nums}\n"
        ret_string += "以下是chunk的内容:\n"
        ret_string += self.m_content
        ret_string += f"\n----这是{idx_text}号RAG chunk的内容分割线----\n"
        return ret_string


class CReportRAGRetrieveResult:
    """RAG召回整体返回结果，业务对外实体"""
    def __init__(self):
        self.m_query: str = ""
        self.m_chunks: List[CReportRAGChunk] = []

    #发给LLM带内容分割线的chunk内容数据
    def GetLLMContent(self):
        ret_string = "\n----这是一次RAG请求返回结果的分割线----\n"
        ret_string += f"RAG的query:{self.m_query}\n"
        ret_string += f"下面是各chunk的内容\n"
        for _chunk in self.m_chunks:
            ret_string += _chunk.GetLLMContent()
        ret_string += "\n----这是一次RAG请求返回结果的分割线----\n"
        return ret_string


class CReportRAGRetrieveMergeResult:
    """多个query召回结果合并、去重输出对象"""
    def __init__(self, report_name, retrieve_result_list: List["CReportRAGRetrieveResult"]):
        self.m_report_name = report_name
        self.m_querys: List[str] = []
        self.m_chunks: List[CReportRAGChunk] = []
        seen_idx = set()
        # 支持传入空列表
        for res in retrieve_result_list:
            self.m_querys.append(res.m_query)
            for chunk in res.m_chunks:
                idx = chunk.m_index
                if idx is None:
                    # 元数据异常兜底，直接保留该chunk，不参与去重
                    self.m_chunks.append(chunk)
                else:
                    if idx not in seen_idx:
                        seen_idx.add(idx)
                        self.m_chunks.append(chunk)

    def GetLLMContent(self) -> str:
        """获取合并去重之后完整上下文，直接喂给LLM"""
        ret_string = f"\n=====【{self.m_report_name}批量RAG召回合并结果】=====\n"
        ret_string += f"查询列表:{self.m_querys} =====\n"
        ret_string += f"chunk数量:{self.GetCount()} =====\n"
        for ck in self.m_chunks:
            ret_string += ck.GetLLMContent()
        ret_string += f"\n=====【{self.m_report_name}批量RAG召回合并结果】=====\n"
        return ret_string

    def GetCount(self):
        return len(self.m_chunks)


class CFinancialReport:
    """单份财报实体"""
    def __init__(self, stock_code: str, company_name: str, report_date: CReportDate):
        self.m_stock_code: str = stock_code
        self.m_company_name = company_name
        self.m_report_date: CReportDate = report_date
        self.m_summary: str = ""
        #: akshare转换后的中文可读文本
        self.m_akshare: str = ""
        #: akshare原始json字典（不再存字符串）
        self.m_akshare_json: Dict = dict()
        #: pdf解析原始带页码标记文本，用户上传PDF时填充
        self.m_raw_pdf_text: str = ""
        #: datasink.ing来源markdown原文，来自ds_content.md
        self.m_raw_ds_text: str = ""
        #: datasink.ing财报名，来自ds_base_info.json m_title
        self.m_ds_title: str = ""

    def GetName(self):
        return f"{self.m_company_name}{self.m_report_date.GetName()}"

    def GetReportDir(self) -> str:
        """
        获取该财报对应的本地存储目录
        示例: $data_dir/company/600519/reports/annual‑2022‑0
        """
        cfg = CConfigManager.GetInstance()
        base_reports_dir = cfg.GetReportBaseDir(self.m_stock_code)
        key = self.m_report_date.GetKey()
        dir_path = os.path.join(base_reports_dir, key)
        return dir_path

    def GetRawText(self) -> str:
        """
        获取财报原始全文，多数据源兼容
        优先级：m_raw_pdf_text(PDF解析‑raw.txt) > m_raw_ds_text(datasinking‑ds_content.md)
        """
        if self.m_raw_pdf_text:
            return self.m_raw_pdf_text
        return self.m_raw_ds_text


    def Save(self) -> bool:
        """
        保存：akshare.json、akshare.txt、summary.md到本地目录
        注意：ds_base_info.json / ds_content.md 由 CDSFile.Save() 负责写入，本函数不处理
        当 m_raw_pdf_text 为空，跳过 raw.txt 写入
        目录不存在自动创建；返回True成功，False异常
        """
        report_dir = self.GetReportDir()
        os.makedirs(report_dir, exist_ok=True)

        # m_raw_pdf_text为空则跳过raw.txt
        if self.m_raw_pdf_text:
            raw_path = os.path.join(report_dir, "raw.txt")
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(self.m_raw_pdf_text)

        # summary.md
        sum_path = os.path.join(report_dir, "summary.md")
        with open(sum_path, "w", encoding="utf-8") as f:
            f.write(self.m_summary)
        # akshare.json 字典写文件
        if self.m_akshare_json:
            json_path = os.path.join(report_dir, "akshare.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.m_akshare_json, f, ensure_ascii=False, indent=2)
        # akshare.txt
        txt_path = os.path.join(report_dir, "akshare.txt")
        if self.m_akshare:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(self.m_akshare)
        return True

    def CheckOK(self):
        if not self.m_summary:
            return False
        return True

    def Load(self) -> bool:
        """
        从本地磁盘加载 akshare.json / akshare.txt / summary.md / raw.txt
        同时检测读取 ds_base_info.json + ds_content.md（datasinking产物）
        返回True加载成功；文件缺失/异常返回False
        """
        try:
            report_dir = self.GetReportDir()
            if not os.path.isdir(report_dir):
                return False
            json_path = os.path.join(report_dir, "akshare.json")
            txt_path = os.path.join(report_dir, "akshare.txt")
            sum_path = os.path.join(report_dir, "summary.md")
            raw_path = os.path.join(report_dir, "raw.txt")

            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    self.m_akshare_json = json.load(f)
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8") as f:
                    self.m_akshare = f.read()
            if os.path.exists(sum_path):
                with open(sum_path, "r", encoding="utf-8") as f:
                    self.m_summary = f.read()
            if os.path.exists(raw_path):
                with open(raw_path, "r", encoding="utf-8") as f:
                    self.m_raw_pdf_text = f.read()

            # 读取 datasinking 产出文件 ds_base_info.json + ds_content.md，两个必须同时存在
            ds_info_path = os.path.join(report_dir, "ds_base_info.json")
            ds_content_path = os.path.join(report_dir, "ds_content.md")
            self.m_raw_ds_text = ""
            self.m_ds_title = ""
            if os.path.exists(ds_info_path) and os.path.exists(ds_content_path):
                try:
                    with open(ds_info_path, "r", encoding="utf-8") as f:
                        ds_info = json.load(f)
                    self.m_ds_title = ds_info.get("m_title", "")
                    _company_name = ds_info.get("m_stock_name", "")
                    if _company_name:
                        self.m_company_name = _company_name

                    with open(ds_content_path, "r", encoding="utf-8") as f:
                        self.m_raw_ds_text = f.read()
                except Exception as e:
                    LogStack(e, f"CFinancialReport.Load read ds_* file fail dir={report_dir}")
                    self.m_raw_ds_text = ""
                    self.m_ds_title = ""

            return self.CheckOK()
        except Exception as e:
            print(f"CFinancialReport.Load 异常 {e}")
            return False

    # ========== 访问函数 ==========
    def GetSummary(self) -> str:
        return self.m_summary

    def GetAkshareText(self) -> str:
        return self.m_akshare

    def GetAkshareJsonDict(self) -> Dict:
        return self.m_akshare_json

    def GetReportName(self):
        return f"[{self.m_company_name}({self.m_stock_code})]{self.m_report_date.GetName()}"

    def GetBaseInfo(self):
        splitor = f"\n------------{id(self)} {self.GetReportName()}基础信息分割线 {id(self)}------------\n"
        ret_string = ""
        ret_string += splitor
        ret_string += f"akshare 信息:\n"
        ret_string += f"{self.m_akshare}\n"
        ret_string += f"财报摘要:\n"
        ret_string += f"{self.m_summary}\n"
        ret_string += splitor
        return ret_string

    def __repr__(self):
        return f"<CFinancialReport stock={self.m_stock_code}, {self.m_report_date!r}>"

    def GetCollectionName(self) -> str:
        """获取该财报对应的RAG collection唯一名称: stockcode‑reportdatekey"""
        stock = self.m_stock_code
        date_key = self.m_report_date.GetKey()
        return f"{stock}-{date_key}"

    def RAGImportRawText(self) -> bool:
        """
        将当前财报原始完整文本送入RAG向量化入库
        默认参数 chunk_size=1300, over_lap_size=250
        m_over_write=True：入库前清空本collection旧数据
        注意：不捕获异常，异常向上抛出；执行完整成功返回True
        """
        coll_name = self.GetCollectionName()
        raw_text = self.GetRawText()

        LogInfo(f"CFinancialReport.RAGImportRawText coll_name:{coll_name} len:{len(raw_text)}")

        req = CRAGBatchIngestion(
            m_collection_name=coll_name,
            m_content=raw_text,
            m_over_write=True,
            m_chunk_size=1300,
            m_over_lap_size=250
        )
        mgr = CRAGManager.GetInstance()
        ok = mgr.BatchIngestion(req)
        return ok

    def __RAGRetrieve(self, query: str) -> CReportRAGRetrieveResult:
        """
        【私有】底层单query向量召回
        :param query: 用户自然语言查询字符串
        :return: CReportRAGRetrieveResult
        ⚠️ 不捕获异常，网络/向量/qdrant异常直接向外抛出，上层调用方做try‑except
        """
        coll_name = self.GetCollectionName()
        mgr = CRAGManager.GetInstance()
        rag_req = CRAGRetrieveRequest(
            m_collection_name=coll_name,
            m_query=query,
            m_top_k=4,
            m_score_threshold=0.5
        )
        rag_resp = mgr.Retrieve(rag_req)
        # 底层pydantic模型 → 业务层实体，解耦RAG底层实现
        res = CReportRAGRetrieveResult()
        res.m_query = rag_resp.m_query
        for item in rag_resp.m_chunks:
            chunk = CReportRAGChunk()
            chunk.m_content = item.m_content
            chunk.SetIndex(item.GetIndex())
            chunk.CalPageNum()
            res.m_chunks.append(chunk)
        return res

    def RAGRetrieves(self, querys: List[str]) -> CReportRAGRetrieveMergeResult:
        """
        批量多query召回，自动合并、按m_index去重
        :param querys: 查询字符串列表
        :return: CReportRAGRetrieveMergeResult
        ⚠️任意一个query异常直接向上抛出，不做部分成功容错
        """
        ret_list = []
        for q in querys:
            single_res = self.__RAGRetrieve(q)
            ret_list.append(single_res)
        res = CReportRAGRetrieveMergeResult(self.GetReportName(), ret_list)
        LogInfo(f"RAGRetrieves report:{self.GetReportName()}, querys:{querys}, hit:{res.GetCount()}")
        return res


def TestSave():
    """测试存盘：茅台600519 2022年报，写入磁盘文件"""
    print("\n==== TestSave 测试：保存茅台2022年报 ====")
    rd = CReportDate(TYPE_ANNUAL, year=2022, quarter=0)
    rep = CFinancialReport(stock_code="600519", company_name="贵州茅台", report_date=rd)
    mock_json = {
        "REPORT_DATE": "2022-12-31",
        "REPORT_TYPE": "年报",
        "OPERATE_INCOME": 1275.54,
        "PARENT_NETPROFIT": 627.16
    }
    mock_text = """【年报 2022‑12‑31】
营业总收入:1275.54 亿元
归母净利润:627.16 亿元
"""
    mock_summary = """贵州茅台酒股份有限公司2022年度报告摘要模拟文本。
公司全年营收1275.54亿元，归母净利润627.16亿元。
"""
    rep.m_akshare_json = mock_json
    rep.m_akshare = mock_text
    rep.m_summary = mock_summary
    dir_path = rep.GetReportDir()
    print(f"目标存储目录: {dir_path}")
    ok = rep.Save()
    if ok:
        print("✅ TestSave 成功，文件已落地磁盘")
    else:
        print("❌ TestSave 失败")


def TestLoad():
    print("\n==== TestLoad ====")
    rd = CReportDate(TYPE_ANNUAL, year=2022, quarter=0, half=0)
    rep = CFinancialReport(stock_code="600519", company_name="xxxx", report_date=rd)
    dir_path = rep.GetReportDir()
    print(f"读取目录: {dir_path}")  
    ok = rep.Load()
    if ok:
        print(f"✅ TestLoad {rep.GetReportName()} 加载成功")
        print("\n--- akshare.txt片段 ---")
        print(rep.GetAkshareText()[:300])
        print("\n--- summary.txt片段 ---")
        print(rep.GetSummary()[:300])
        print("\n--- akshare_json 营业收入 ---")
        print(f"OPERATE_INCOME = {rep.GetAkshareJsonDict().get('OPERATE_INCOME')}\n")

        print(f"财报原文大小:{len(rep.GetRawText())}\n")
        print("------------------------\n")
        print(f"{rep.GetRawText()[:200]}\n")        
    else:
        print("⚠️ TestLoad 加载失败，磁盘不存在该财报，请先运行TestSave生成数据")


def TestFinancialReportRAGImport():
    """测试：财报对象RAG入库"""
    print("\n==== TestFinancialReportRAGImport start ====")
    rd = CReportDate(TYPE_ANNUAL, year=2024, quarter=0, half=0)
    report = CFinancialReport(stock_code="600519", company_name="贵州茅台", report_date=rd)
    report.Load()

    coll = report.GetCollectionName()
    print(f"collection name: {coll}")
    try:
        ok = report.RAGImportRawText()
        print(f"RAGImportRawText result: {ok}")
    except Exception as e:
        print(f"RAG入库异常: {e}")
        return False
    print("==== TestFinancialReportRAGImport done ====")
    return ok


def TestFinancialReportRAGRetrieve():
    """测试：财报对象RAG召回（依赖已完成入库）"""
    print("\n==== TestFinancialReportRAGRetrieve start ====")
    rd = CReportDate(TYPE_ANNUAL, year=2024, quarter=0, half=0)
    report = CFinancialReport(stock_code="600519", company_name="贵州茅台", report_date=rd)
    querys = ["营业收入构成", "毛利率"]
    merge_res = report.RAGRetrieves(querys)
    print(f"merge querys:{merge_res.m_querys}, chunk数量:{len(merge_res.m_chunks)}")
    print(merge_res.GetLLMContent())
    print("==== TestFinancialReportRAGRetrieve done ====")
    return True


if __name__ == "__main__":
    TestFinancialReportRAGRetrieve()