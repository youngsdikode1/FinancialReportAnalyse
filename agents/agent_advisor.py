import json
from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
import agents.agent_intent
import agents.agent_orchestrator
import agents.agent_stock_check
from utils.logs import PrintLog, LogInfo
from utils.llm_request import CLLMRequest
import task_analyse  
from companys.company_manager import CCompanyManager
from financials.report_date import CReportDate, TYPE_ANNUAL, TYPE_HALF, TYPE_QUARTER
import users.defs

MAX_CONTEXT_RECORDS = 50



# ====================== CAgentAdvisor ======================

class CAgentAdvisor:
    def __init__(self, user):
        self.m_user = user
        self.m_session_id = user.GetSessionID()
        self.m_intent_agent = agents.agent_intent.CAgentIntent(self.m_session_id)


        self.m_extra_demand_agent = agents.agent_stock_check.CAgentDemandExtra()
        self.m_agent_stock_check = agents.agent_stock_check.CAgentStockCheck()
        self.m_orchestrator = agents.agent_orchestrator.CAgentOrchestrator(self.m_session_id)

        self.m_task_id_inc = 30000
        self.m_cur_task = None


    def BuildContextText(self) -> str:
        chat_part = self.m_user.GetHistory().GetLLMUserHistory()
        return chat_part


    def CancelTask(self):
        if not self.m_cur_task:
            return 

        self.m_cur_task.SetCancel()

    def IsTaskRunning(self):
        if not self.m_cur_task:
            return False

        if self.m_cur_task.IsTimeOut():
            return False

        return self.m_cur_task.IsRunning()


    def HandleTaskCreate(self, intent_result: agents.agent_intent.CIntentResult, user_input: str) -> str:

        current_task = None
        LogInfo(f"CAgentAdvisor.HandleTaskCreate analysis_demand:{intent_result.analysis_demand}")

        extra_result = self.m_extra_demand_agent.Run(intent_result.analysis_demand)

        max_year = 5
        if CReportDate.IsYearRangeTooOld(extra_result.year_range, max_year):
            LogInfo(f"CAgentAdvisor.HandleTaskCreate year failed, analysis_demand:{intent_result.analysis_demand}")
            return f"演示版暂时不支持分析超过{max_year}年的财报,您可以调整一下时间范围"

        new_task_id = f"task_{self.m_task_id_inc}"
        self.m_task_id_inc += 1

        new_task = task_analyse.CTaskAnalysis(new_task_id)
        new_task.m_year_range = extra_result.year_range
        new_task.m_analysis_requirement = intent_result.analysis_demand or ""

        LogInfo(f"CAgentAdvisor.HandleTaskCreate analysis_demand:{intent_result.analysis_demand}")

        stock_check_result = self.m_agent_stock_check.Run(extra_result.target_stocks, new_task.m_analysis_requirement)
        if not stock_check_result.is_valid:
            LogInfo("HandleTaskCreate stock_check_result failed")
            return stock_check_result.msg


        company_info_list = stock_check_result.companys
        LogInfo(f"HandleTaskCreate company_info_list: {company_info_list}")

        company_mgr = CCompanyManager.GetInstance()
        for comp_dict in company_info_list:
            _company = company_mgr.CreateCompany(comp_dict)
            new_task.AddStock(_company.GetStockCode())

        # 切 running，锁输入框
        self.m_user.RefreshInputUI(users.defs.SYS_STATE_RUNNING, f"{intent_result.analysis_demand}，正在进行中，请稍候...")

        hints = f"开始执行任务:{intent_result.analysis_demand}\n\n"
        hints += new_task.GetEstimateDesc() 
        hints += "\n\n任务正式开始，请您耐心等待，并可留意右边状态栏进度"

        self.m_user.AddBotMessage(hints)

        self.m_cur_task = new_task
        self.m_cur_task.SetStatus(task_analyse.TASK_STATUS_RUNNING)
        self.m_orchestrator.SetTask(self.m_cur_task)

        report = self.m_orchestrator.Run(self.m_cur_task)
        if self.m_cur_task.IsCancel():
            self.m_cur_task = None
            return None

        self.m_cur_task.m_full_analysis_result = report
        self.m_cur_task.SetStatus(task_analyse.TASK_STATUS_FINISHED)
        self.m_cur_task = None

         # 任务完成，切回 idle
        self.m_user.RefreshInputUI(users.defs.SYS_STATE_IDLE, None)

        return report



    def HandleUserInput(self, user_input: str) -> str:
        context_str = self.BuildContextText()

        intent_result = self.m_intent_agent.Run(context_str)

        PrintLog("\n===== 意图识别完整JSON =====")
        json_data = intent_result.model_dump_json(indent=2)
        PrintLog(json_data)
        PrintLog(f"\n===== 意图判定Reason =====")
        PrintLog(f"intent_type: {intent_result.intent_type}")

        LogInfo(f"HandleUserInput user_input:{user_input} intent:{intent_result.intent_type}")
        LogInfo(f"HandleUserInput intent json:\n{json_data}")

        ans = ""
        if intent_result.intent_type in agents.agent_intent.INTENT_TASK_START_CONFIRM:
            ans = self.HandleTaskCreate(intent_result, user_input)            

        else:
            ans = intent_result.GetResponse()


        LogInfo("===================== HandleUserInput end =====================\n")        
        return ans
