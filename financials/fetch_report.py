from typing import List, Optional
from datetime import datetime
from financials import data_sinking
from financials.financial_report import CFinancialReport
from financials.financial_report_manager import CFinancialReportManager
from companys.company import CCompany
from companys.company_manager import CCompanyManager
from utils.logs import LogInfo, LogStack
from utils.llm_request import CLLMRequest
from financials.report_date import CReportDate, TYPE_ANNUAL, TYPE_HALF, TYPE_QUARTER
from events.event_manager import CEventManager, PostWorkFlowMessage

# ========= LLM 财报摘要提示词 =========
FIN_REPORT_SUMMARY_PROMPT = """
你是财报分析助手，请阅读下面这份上市公司财报完整markdown文本，生成一份简洁中文摘要。
【财报研读质量要求】（必须全部遵守）
1. 用markdown分章节输出，优先按财报原生章节组织；
2. 覆盖：公司概况、经营业绩、分产品/分地区业务、成本费用、资产负债、现金流、分红、重大事项、风险提示、审计意见；
3. 所有数字必须来自原文，禁止编造；指标冲突要标⚠️数据冲突；区分不同统计口径；
4. 输出字数尽量控制在5000‑7000token之间，禁止过度简略压缩，在限额内尽可能详实展开，不要省略业务说明、口径备注，不要只罗列表格数字。
=====财报原文开始=====
{content}
=====财报原文结束=====
"""


class CDSFetchReports:
    """
    通过 datasinking 获取财报，组装生成 CFinancialReport 并归档
    注意：m_report_manager 需要外部传入实例，不内部调用GetInstance，对齐CAgentPDFProcessor流程
    """
    def __init__(self):
        self.m_session_id: str = ""
        self.m_stock_code: str = ""
        self.m_year_range: str = ""  # "2020.01月‑2024.12月"
        self.m_report_manager: Optional[CFinancialReportManager] = None  # 外部传入，不内部GetInstance

    def CalMissReports(self) -> List[CReportDate]:
        LogInfo(f"CDSFetchReports.CalMissReports session={self.m_session_id}, stock={self.m_stock_code}, range={self.m_year_range}")

        # ConvertReportDate直接返回该时间范围全部CReportDate对象列表
        expect_rds: List[CReportDate] = CReportDate.ConvertReportDate(self.m_year_range)

        miss_list: List[CReportDate] = []
        for rd in expect_rds:
            if not self.m_report_manager.GetReport(rd):
                miss_list.append(rd)

        # 按时间从小到大排序
        miss_list.sort(key=lambda x: (x.m_year, x.m_type, x.m_quarter))
        LogInfo(f"CDSFetchReports.CalMissReports miss count={len(miss_list)}")
        return miss_list

    def MakeReport(self, report_date: CReportDate) -> Optional[CFinancialReport]:
        """
        制作一份CFinancialReport并归档
        1.LoadDSFile拿到CDSFile，新建CFinancialReport填充字段
        2.组装prompt + 财报原文，调用llm.InvokeStructured 获取摘要，失败警告，summary置空继续
        3.参考CAgentPDFProcessor._node_build_report_entity流程，添加进管理器并Save
        """
        LogInfo(f"CDSFetchReports.MakeReport stock={self.m_stock_code}, rd={report_date.GetKey()}, session={self.m_session_id}")

        # step1 读取本地CDSFile
        cds_file = data_sinking.LoadDSFile(self.m_stock_code, report_date)
        if cds_file is None:
            LogInfo(f"CDSFetchReports.MakeReport LoadDSFile return None, skip rd={report_date.GetKey()}")
            return None

        rep = CFinancialReport(
            stock_code=cds_file.m_stock_code,
            company_name=cds_file.m_stock_name,
            report_date=report_date
        )
        rep.m_raw_ds_text = cds_file.m_content
        rep.m_ds_title = cds_file.m_title
        # m_raw_pdf_text 保持为空，Save会跳过raw.txt写入

        PostWorkFlowMessage(self.m_session_id, f"⏳正在处理 {rep.GetName()}...")

        # step2 LLM生成摘要：拼接提示词+财报原文
        prompt = FIN_REPORT_SUMMARY_PROMPT.format(content=rep.m_raw_ds_text)

        llm = CLLMRequest()
        llm.SWitchToHeavyModel()
        output_text = llm.Invoke(prompt)
        rep.m_summary = output_text.strip()

        # step3 参考 _node_build_report_entity 流程，使用外部传入的 m_report_manager，不要GetInstance
        company = CCompanyManager.GetInstance().GetCompany(self.m_stock_code)
        self.m_report_manager.AddReport(rep)
        rep.Save()

        PostWorkFlowMessage(self.m_session_id, f"💾{rep.GetName()}数据入库中...")
        rep.RAGImportRawText()

        PostWorkFlowMessage(self.m_session_id, f"💡{rep.GetName()}处理完成!!")

        LogInfo(f"CDSFetchReports.MakeReport done rd={report_date.GetKey()}")
        return rep

    def Start(self) -> List[CReportDate]:
        """
        正式开始获取财报
        1.CalMissReports 获取缺失报告列表
        2.从miss_rds提取最小、最大年份，缩小窗口调用FetchDSFiles下载
        3.遍历缺失列表调用MakeReport，单份失败打日志跳过，继续下一个
        返回本次处理的CReportDate列表
        """

        handled_list: List[CReportDate] = []
        company = CCompanyManager.GetInstance().GetCompany(self.m_stock_code)
        if not company:
            LogInfo(f"CDSFetchReports.Start error: company:{self.m_stock_code} is None")
            return handled_list

        self.m_report_manager = company.GetReportManager()

        miss_rds = self.CalMissReports()
        if len(miss_rds) == 0:
            LogInfo(f"CDSFetchReports.Start no miss reports, session={self.m_session_id}")
            return handled_list

        miss_desc = f"🔧{company.GetNameCode()}在系统本地缺少财报:"
        for _report_date in miss_rds:
            miss_desc += f"{_report_date.GetName()} "

        miss_desc += ",我将开始下载"


        PostWorkFlowMessage(self.m_session_id, miss_desc)

        # 从缺失报告列表提取最小/最大年份，构造下载窗口
        miss_years = [rd.m_year for rd in miss_rds]
        min_year = min(miss_years)
        max_year = max(miss_years)
        dl_start = f"{min_year}-01-01"
        dl_end = f"{max_year}-12-31"
        LogInfo(f"CDSFetchReports.Start download window [{dl_start} ~ {dl_end}], miss count={len(miss_rds)} stock={self.m_stock_code}")

        # 下载，优先本地，缺失触发http
        _ = data_sinking.FetchDSFiles(self.m_stock_code, dl_start, dl_end)

        # 使用CalMissReports返回有序的miss_rds，逐个处理
        for rd in miss_rds:
            try:
                rep = self.MakeReport(rd)
                if rep is not None:
                    handled_list.append(rd)
            except Exception as e:
                LogStack(e, f"CDSFetchReports.Start MakeReport fail skip rd={rd.GetKey()} stock={self.m_stock_code}")
                continue
        LogInfo(f"CDSFetchReports.Start finish handled count={len(handled_list)}, miss total={len(miss_rds)}")
        return handled_list


if __name__ == "__main__":
    """测试用例：注意使用前必须赋值 m_report_manager"""
    LogInfo("==== CDSFetchReports test start ====")
    # 测试时示例，实际业务从agent上下文拿到manager实例
    # from financials.financial_report_manager import CFinancialReportManager
    obj = CDSFetchReports()
    obj.m_session_id = "test-session"
    obj.m_stock_code = "002069"
    obj.m_year_range = "2020.01月‑2024.12月"
    obj.Start()


    LogInfo("==== CDSFetchReports test end ====")