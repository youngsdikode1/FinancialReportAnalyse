from utils.logs import PrintLog, LLMRequestLog
from typing import List, TypedDict, Optional, Any, Literal
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from utils.config import CConfigManager
from langchain_deepseek import ChatDeepSeek
import json
import utils.commons
from datetime import datetime

# ====================== 工具函数 ======================
def EstimateTokenCount(text: str) -> int:
    if not text:
        return 0
    cn_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
    en_text = ''.join([c for c in text if not '\u4e00' <= c <= '\u9fff'])
    en_tokens = int(len(en_text.split()) * 0.75)
    return cn_chars + en_tokens


# 用于demo测试的结构化模型
class DemoSimpleResp(BaseModel):
    content: str = Field(description="回答内容")
    sentiment: str = Field(description="情绪：positive/negative/neutral")


# ====================== CLLMRequest ======================
class CLLMRequest:
    def __init__(
        self,
        base_url: str = CConfigManager.GetInstance().GetLLMUrl(),
        model_name: str = CConfigManager.GetInstance().GetLLMModel(),
        timeout: int = 30,
        bDebugLog: bool = True
    ):
        self.m_baseUrl = base_url
        self.m_modelName = model_name
        self.m_timeout = timeout
        self.m_bDebugLog = bDebugLog
        self.m_llm = ChatOllama(
            base_url=self.m_baseUrl,
            model=self.m_modelName,
            timeout=self.m_timeout
        )

    def SWitchToHeavyModel(self):
        self.m_baseUrl = CConfigManager.GetInstance().GetHeavyLLMUrl()
        self.m_modelName = CConfigManager.GetInstance().GetHeavyLLMModel()
        self.m_llm = ChatOllama(
            base_url=self.m_baseUrl,
            model=self.m_modelName,
            timeout=self.m_timeout
        )

    def SWitchToDeepseekModel(self):
        """切换到DeepSeek Flash，修复原BUG：不再把model_name当做api_key"""
        self.m_baseUrl = CConfigManager.GetInstance().GetDeepSeekLLMUrl()
        self.m_modelName = CConfigManager.GetInstance().GetDeepSeekLLMModel()
        api_key = CConfigManager.GetInstance().GetDeepSeekKey()
        self.m_llm = ChatDeepSeek(
            model=self.m_modelName,
            api_key=api_key,
            base_url=self.m_baseUrl,
            temperature=0.1,
            max_tokens=12000,
            timeout=240,
            max_retries=2,
            extra_body={
                "thinking": {"type": "disabled"}  # 关键：关闭思考模式，允许tool_choice
            }

        )

    def SetDebugLog(self, enable: bool):
        self.m_bDebugLog = enable

    def Invoke(self, prompt: str, temp_params: Optional[dict] = None) -> str:


        if CConfigManager.GetInstance().GetValue("use_deepseek_only"):
            self.SWitchToDeepseekModel()

        dt = datetime.now()
        s_datetime = dt.strftime("%Y-%m-%d %H:%M:%S")

        llm = self.m_llm
        if temp_params is not None:
            llm = llm.with_config(temp_params)
        input_tokens = EstimateTokenCount(prompt)


        LLMRequestLog(f"\n********************* [CLLMRequest DebugLog] *********************")
        caller = utils.commons.GetCaller().function
        LLMRequestLog(s_datetime)            
        LLMRequestLog(f"caller: {caller}")
        LLMRequestLog(f"Model: {self.m_modelName}")
        LLMRequestLog(f"Input estimated token: {input_tokens}")
        LLMRequestLog(f"Prompt:\n{prompt}\n")   

        resp = llm.invoke(prompt)
        output_text = resp.content
        output_tokens = EstimateTokenCount(output_text)

        LLMRequestLog("\n--------------------------------------\n")    
        LLMRequestLog(f"Output estimated token: {output_tokens}")
        LLMRequestLog(f"Response:\n{output_text}\n")
        LLMRequestLog("********************************************************************\n")
        return output_text

    def InvokeStructured(self, prompt: str, schema_cls: type[BaseModel], temp_params: Optional[dict] = None) -> Any:

        if CConfigManager.GetInstance().GetValue("use_deepseek_only"):
            self.SWitchToDeepseekModel()


        structured_llm = self.m_llm.with_structured_output(schema_cls)
        if temp_params is not None:
            structured_llm = structured_llm.with_config(temp_params)
        input_tokens = EstimateTokenCount(prompt)

        dt = datetime.now()
        s_datetime = dt.strftime("%Y-%m-%d %H:%M:%S")

        LLMRequestLog(f"\n********************* [CLLMRequest Structured DebugLog] *********************")
        caller = utils.commons.GetCaller().function
        LLMRequestLog(s_datetime)                       
        LLMRequestLog(f"caller: {caller}")
        LLMRequestLog(f"Model: {self.m_modelName}")
        LLMRequestLog(f"Input estimated token: {input_tokens}")        
        LLMRequestLog(f"Prompt:\n{prompt}\n")


        result = structured_llm.invoke(prompt)
        output_json_str = result.model_dump_json()
        output_tokens = EstimateTokenCount(output_json_str)

        pretty_json_str = utils.commons.PrettyJson(output_json_str)

        LLMRequestLog("\n--------------------------------------\n")
        LLMRequestLog(f"Output estimated token: {output_tokens}")
        if pretty_json_str:
            LLMRequestLog(f"Structured Result JSON:\n{pretty_json_str}\n")
        else:
            LLMRequestLog(f"Structured Result JSON:\n{output_json_str}\n")     
                        
        LLMRequestLog("********************************************************************\n")

        return result


def DemoTestDeepSeek():
    """
    Demo：测试DeepSeek-Flash连通性，放在llm_request.py，直接运行本文件即可测试
    1. 普通文本调用
    2. 结构化输出调用（模拟财报解析pydantic结构化）
    """
    PrintLog("===== DemoTestDeepSeek start =====")

    llm = CLLMRequest()
    llm.SWitchToDeepseekModel()

    # 测试1：普通文本调用
    prompt_text = "简单回答：A股贵州茅台股票代码是多少？"
    resp_text = llm.Invoke(prompt_text)
    PrintLog(f"【普通文本测试返回】:\n{resp_text}")

    # 测试2：结构化输出测试，模拟财报解析的结构化返回
    prompt_struct = "分析这句话：贵州茅台业绩表现优秀。输出content和sentiment字段。"
    out_struct: DemoSimpleResp = llm.InvokeStructured(prompt_struct, DemoSimpleResp)
    PrintLog(f"【结构化测试返回】content={out_struct.content}, sentiment={out_struct.sentiment}")

    PrintLog("===== DemoTestDeepSeek 全部调用成功 =====")


if __name__ == "__main__":
    DemoTestDeepSeek()