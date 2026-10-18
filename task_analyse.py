import json
from typing import List, TypedDict, Optional, Any, Literal, Set
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
from utils.logs import PrintLog, LogInfo
from companys.company_manager import CCompanyManager
from companys.company import CCompany
import time
from financials.report_date import CReportDate
from utils.config import CConfigManager


TASK_STATUS_NEW_CREATE = "task_new_create"
TASK_STATUS_NEED_REFINE = "task_need_refine"
TASK_STATUS_READY = "task_ready"
TASK_STATUS_RUNNING = "task_running"
TASK_STATUS_FINISHED = "task_finished"
TASK_STATUS_FAILED = "task_failed"
TASK_STATUS_CANCEL = "task_cancel"


TASK_TYPE_SINGLE_STOCK: Literal["single_stock"] = "single_stock"
TASK_TYPE_MULTI_STOCK: Literal["multi_stock"] = "multi_stock"
TASK_TYPE_SECTOR: Literal["sector"] = "sector"


class CTaskAnalysis:
    def __init__(self, task_id: str):
        self.m_task_id = task_id
        self.m_status = TASK_STATUS_NEW_CREATE

        self.m_target_stocks: Set[str] = set() #分析的目标的股票代码集合
        self.m_analysis_requirement: str = ""
        self.m_task_type: Optional[Literal["single_stock", "multi_stock", "sector"]] = None
        self.m_year_range: Optional[str] = None
        self.m_full_analysis_result: Optional[str] = None

        self.m_create_time = (int)(time.time())

    def __repr__(self):
        return f"CTaskAnalysis<status:{self.m_status}, companys:{self.GetCompanyNameCodes()}, year_range:{self.m_year_range}, requirement:{self.m_analysis_requirement}>"

    def IsTimeOut(self):
        now = (int)(time.time())
        return now - self.m_create_time > 60 * 15

    def SetCancel(self):
        self.m_status = TASK_STATUS_CANCEL

    def IsCancel(self):
        return self.m_status == TASK_STATUS_CANCEL

    def GetRequirement(self):
        return self.m_analysis_requirement


    def SetStatus(self, status: str):
        self.m_status = status

    def GetStatus(self):
        return self.m_status

    def SetRunning(self):
        self.m_status = TASK_STATUS_RUNNING

    def IsRunning(self):
        return self.m_status == TASK_STATUS_RUNNING

    def IsFinish(self):
        return self.m_status == TASK_STATUS_FINISHED
    
    def AddStock(self, _id):
        self.m_target_stocks.add(_id)


    def GetCompanyInfos(self) -> List[CCompany]:
        """
        根据task内部 m_target_stocks 股票代码集合，从全局公司管理器查询
        返回 CCompany 实体对象列表；找不到的股票代码直接跳过
        """
        mgr = CCompanyManager.GetInstance()
        result: List[CCompany] = []


        for stock_code in self.m_target_stocks:
            comp = mgr.GetCompany(stock_code)
            if comp is not None:
                result.append(comp)
       
        return result

    def GetCompanyNameCodes(self) -> List[str]:
        """
        根据task内部 m_target_stocks，返回【公司全称】字符串列表
        """
        info_list = self.GetCompanyInfos()
        name_list: List[str] = []
        for comp in info_list:
            name_list.append(comp.GetNameCode())
        return name_list

    def GetCompanyNameCodesStr(self):
        return ",".join(self.GetCompanyNameCodes())


    def GetEstimateDesc(self) -> str:
        """
        预估任务执行时间，返回给用户看的描述字符串。
        规则：
        - 基础时间 10 秒
        - 每份本地缺失的财报 +60 秒
        - 总时间 = 60 * n + 10
        返回格式（缺失时）：
        预计耗时约 3 分钟。
        以下财报本地缺失，需要下载并入库：
        - 贵州茅台 2023年报, 2024年报
        - 五粮液 2024年报
        财报全齐时返回：
        财报已全部具备，预计耗时约 1 分钟以内。
        异常（时间范围解析不出报告期）时返回空字符串。
        """
        # 1. 解析时间范围，拿预期报告期列表
        if not self.m_year_range:
            return ""
        expect_rds = CReportDate.ConvertReportDate(self.m_year_range)
        if len(expect_rds) == 0:
            return ""

        # 2. 逐只股票统计缺失报告期，按公司聚合
        # 结构：{公司名: [报告期名, ...]}
        miss_map = {}
        total_miss = 0

        mgr = CCompanyManager.GetInstance()
        for stock_code in self.m_target_stocks:
            comp = mgr.GetCompany(stock_code)
            if comp is None:
                # 公司不存在，跳过
                continue
            rep_mgr = comp.GetReportManager()
            miss_names = []
            for rd in expect_rds:
                if rep_mgr.GetReport(rd) is None:
                    miss_names.append(rd.GetName())
            if len(miss_names) > 0:
                miss_map[comp.GetName()] = miss_names
                total_miss += len(miss_names)
        
        insert_need_time = CConfigManager.GetInstance().GetValue('rag_insert_need_time', 90)
        # 3. 计算总耗时，四舍五入到整数分钟
        total_seconds = insert_need_time * total_miss + 10
        total_minutes = round(total_seconds / 60)

        # 4. 组装描述
        if total_miss == 0:
            return "财报信息本地已全部具备，预计耗时约 1 分钟以内。"

        lines = []
        lines.append("以下财报本地缺失，系统将自动处理，需要经过 下载->摘要->入库 等过程:")
        for comp_name, miss_names in miss_map.items():
            lines.append(f"- {comp_name} {', '.join(miss_names)}")

        lines.append(f"任务总体预计耗时约 {total_minutes} 分钟。")    
        return "\n".join(lines)


def TestGetEstimateDesc():
    """
    测试 GetEstimateDesc：
    标的：贵州茅台(600519)、五粮液(000858)
    年份：2015.01月-2016.12月
    预期输出：报告本地缺失情况 + 预计耗时
    """
    PrintLog("==== TestGetEstimateDesc ====")

    # 1. 确保两家公司在管理器里存在（首次运行会创建并落盘 base_info.json）
    comp_mgr = CCompanyManager.GetInstance()
    comp_mgr.CreateCompany({
        "name": "贵州茅台",
        "stock_code": "600519",
        "fullname": "贵州茅台酒股份有限公司",
        "desc": "中国白酒行业龙头企业，主要从事茅台酒及系列酒的研发、生产和销售"
    })
    comp_mgr.CreateCompany({
        "name": "五粮液",
        "stock_code": "000858",
        "fullname": "宜宾五粮液股份有限公司",
        "desc": "国内浓香型白酒龙头企业，主要从事五粮液系列白酒产品的生产、研发与销售"
    })

    # 2. 构造任务
    task = CTaskAnalysis("test_estimate_001")
    task.AddStock("600519")
    task.AddStock("000858")
    task.m_year_range = "2015.01月-2016.12月"

    print(task.GetEstimateDesc())

if __name__ == "__main__":
    TestGetEstimateDesc()