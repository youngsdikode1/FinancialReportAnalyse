from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph
import json
from utils.logs import PrintLog, LogInfo
import utils.llm_request
from utils.config import CConfigManager

import agents.agent_advisor


# ====================== Main ======================

def Main():
    PrintLog("===== Mock‑Demo 财报Agent（LangChain+LangGraph + Ollama Qwen2.5:7B） =====")
    PrintLog(f"Ollama地址：{CConfigManager.GetInstance().GetLLMUrl()}, 模型：{CConfigManager.GetInstance().GetLLMModel()}")

    CConfigManager.GetInstance()

    advisor = agents.agent_advisor.CAgentAdvisor()
    while True:
        user_in = input("\n>> 请输入你的指令 (exit退出): ")
        if user_in.strip().lower() == "exit":
            PrintLog("程序退出")
            break
        output = advisor.HandleUserInput(user_in)
        PrintLog(f"\n【Agent输出】\n{output}")

if __name__ == "__main__":
    Main()