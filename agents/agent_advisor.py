import json
from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph
import agents.agent_intent
import agents.agent_task_info_refine
import agents.agent_orchestrator
import agents.agent_stock_check
from utils.logs import PrintLog, LogInfo
from utils.llm_request import CLLMRequest
import task_analyse  
from companys.company_manager import CCompanyManager

MAX_CONTEXT_RECORDS = 50



# ====================== CAgentAdvisor ======================

class CAgentAdvisor:
    def __init__(self, session_id):
        self.m_session_id = session_id
        self.m_intent_agent = agents.agent_intent.CAgentIntent(self.m_session_id)


        self.m_task_refine_agent = agents.agent_task_info_refine.CAgentTaskInfoRefine()
        self.m_agent_stock_check = agents.agent_stock_check.CAgentStockCheck()
        self.m_orchestrator = agents.agent_orchestrator.CAgentOrchestrator(self.m_session_id)
        self.m_chat_history = []
        self.m_task_list: List[CTaskAnalysis] = []
        self.m_current_task: Optional[CTaskAnalysis] = None

    def BuildContextText(self) -> str:
        chat_part = ""
        use_records = self.m_chat_history[-MAX_CONTEXT_RECORDS:]
        for msg in use_records:
            chat_part += f"{msg['role']}: {msg['content']}\n"

        task_part = "\n===== 历史已完成任务简要信息 =====\n"
        for task in self.m_task_list:
            task_part += f"taskId:{task.m_task_id}, status:{task.GetStatus()}, target:{task.GetCompanyNameCodesStr()}, summary:{task.m_analysis_requirement}\n"
        return chat_part + task_part

    def AppendChatRecord(self, role: str, content: str):
        self.m_chat_history.append({"role": role, "content": content})

    def HandleFinanceQA(self, user_input: str, context_text: str) -> str:


        prompt = f"""
你是财务知识助手，基于对话上下文回答用户财务/股票常识问题。
上下文：
{context_text}
用户问题：{user_input}
"""
        llmRequest = CLLMRequest()
        return llmRequest.Invoke(prompt)

    def HandleTaskCreate(self, intent_result: agents.agent_intent.CIntentResult, user_input: str) -> str:
        # 前置校验：意图抽取标的为空，直接返回，不进入子Agent
        if not intent_result.target_stocks:
            return "未能识别到需要分析的上市公司，请明确给出公司名称或股票代码。"

        self.m_current_task = None

        LogInfo(f"CAgentAdvisor.HandleTaskCreate target_stocks:{intent_result.target_stocks}")
        new_task_id = f"task_{len(self.m_task_list)+1}"
        new_task = task_analyse.CTaskAnalysis(new_task_id)

        new_task.m_year_range = intent_result.year_range
        new_task.m_analysis_requirement = intent_result.raw_analysis_demand or ""


        stock_check_result = self.m_agent_stock_check.Run(intent_result.target_stocks, user_input)
        if not stock_check_result.is_valid:
            self.m_current_task = None
            LogInfo("HandleTaskCreate stock_check_result failed")
            return f"股票信息校验失败, 请检查{intent_result.target_stocks}是否为A股上市公司"

        self.m_current_task = new_task


        company_info_list = stock_check_result.companys
        LogInfo(f"HandleTaskCreate company_info_list: {company_info_list}")

        company_mgr = CCompanyManager.GetInstance()
        for comp_dict in company_info_list:
            _company = company_mgr.CreateCompany(comp_dict)
            self.m_current_task.AddStock(_company.GetStockCode())


        return self.HandleTaskInfoRefine(user_input)
        

    def HandleTaskInfoRefine(self, user_input: str) -> str:
        if self.m_current_task is None:
            return "当前没有待完善的任务，请重新发起财报分析请求。"

        refine_result = self.m_task_refine_agent.Run(self.m_current_task, user_input)

        if not refine_result.success:
            self.m_current_task = None
            return f"{refine_result.fail_reason}。请提供明确信息"

        if refine_result.missing_fields:
            self.m_current_task.SetStatus(task_analyse.TASK_STATUS_NEED_REFINE)
            return f"信息不足，请补充以下内容：{','.join(refine_result.missing_fields)}。"

        if refine_result.ready_to_run:
            self.m_current_task.SetStatus(task_analyse.TASK_STATUS_READY)
            if self.m_current_task.GetCompanyNameCodesStr():
                target = self.m_current_task.GetCompanyNameCodesStr()
            else:
                target = "未指定标的"           

            year = self.m_current_task.m_year_range or "未指定年份"
            req = self.m_current_task.m_analysis_requirement or "未指定分析诉求"
            return (
                #f"任务已确认：分析【{target}】{year}财报。\n"
                f"分析诉求：{req}\n"
                f"确认开始执行吗？回复“确认执行”即可启动。"
            )

        return "任务信息仍不完整，请补充必要字段。"

    def HandleTaskStartConfirm(self) -> str:
        task = self.m_current_task
        if task is None or task.IsFinish():
            return "不存在就绪状态的任务，请先创建并完善财报分析任务。"

        task.SetStatus(task_analyse.TASK_STATUS_RUNNING)
        report = self.m_orchestrator.Run(task)
        task.m_full_analysis_result = report
        task.SetStatus(task_analyse.TASK_STATUS_FINISHED)
        self.m_task_list.append(task)
        self.m_current_task = None

        LogInfo("HandleTaskStartConfirm finish")
        return report

    def HandleTaskFollowUp(self, user_input: str, context_text: str) -> str:
        finished_task = None
        for task in reversed(self.m_task_list):
            if task.m_status == task_analyse.TASK_STATUS_FINISHED:
                finished_task = task
                break

        if finished_task is None:
            return "没有找到已完成的分析任务，请先执行财报分析。"

        prompt = f"""
基于下面这份财报分析报告回答用户追问：
{finished_task.m_full_analysis_result}

对话上下文：
{context_text}
用户提问：{user_input}
"""
        llmRequest = CLLMRequest()
        return llmRequest.Invoke(prompt)

    def HandleUnknownIntent(self) -> str:
        return "抱歉，本系统仅支持财报分析、财务知识问答，无法处理该请求。"

    def HandleUserInput(self, user_input: str) -> str:
        context_str = self.BuildContextText()
        current_task_status = self.m_current_task.m_status if self.m_current_task else None
        intent_result = self.m_intent_agent.Run(context_str, current_task_status, user_input)

        PrintLog("\n===== 意图识别完整JSON =====")
        json_data = intent_result.model_dump_json(indent=2)
        PrintLog(json_data)
        PrintLog(f"\n===== 意图判定Reason =====")
        PrintLog(f"intent_type: {intent_result.intent_type}")
        PrintLog(f"reason: {intent_result.reason}")


        LogInfo(f"HandleUserInput intent:{intent_result.intent_type} user_input:{user_input}")
        LogInfo(f"HandleUserInput intent json:\n{json_data}")

        ans = ""
        if intent_result.intent_type == agents.agent_intent.INTENT_TASK_CREATE:
            ans = self.HandleTaskCreate(intent_result, user_input)
        elif intent_result.intent_type == agents.agent_intent.INTENT_TASK_INFO_REFINE:
            ans = self.HandleTaskInfoRefine(user_input)
        elif intent_result.intent_type == agents.agent_intent.INTENT_TASK_START_CONFIRM:
            ans = self.HandleTaskStartConfirm()
        elif intent_result.intent_type == agents.agent_intent.INTENT_TASK_FOLLOWUP:
            ans = self.HandleTaskFollowUp(user_input, context_str)
        elif intent_result.intent_type == agents.agent_intent.INTENT_FINANCE_QA:
            ans = self.HandleFinanceQA(user_input, context_str)
        else:
            ans = self.HandleUnknownIntent()

        self.AppendChatRecord("user", user_input)
        self.AppendChatRecord("assistant", ans)

        LogInfo("===================== HandleUserInput end =====================\n")        
        return ans
