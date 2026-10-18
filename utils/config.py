import json
import os
from typing import Dict, Any
from utils.logs import PrintLog


class CConfigManager:
    """
    单例配置管理器
    1. 内置默认配置 m_configs
    2. 启动自动读取项目根目录 config.json
    3. json内存在的key，覆盖默认值；不存在的key保留默认值
    4. 加载完成后打印全部配置
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.__init_once()
        return cls._instance

    @classmethod
    def GetInstance(cls) -> "CConfigManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init_once(self):
        # 默认配置，在这里预先写好所有默认key-value
        self.m_configs: Dict[str, Any] = {
            "llm_url": "http://127.0.0.1:11434",
            "llm_model_name": "qwen3.5",
            "heavy_llm_url": "http://127.0.0.1:11434",
            "heavy_llm_model_name": "qwen3.5",
            "data_dir": "./datas",
        }

        # 读取根目录 config.json 合并配置
        self._load_config_json()
        # 加载完成，打印全部配置
        self._print_all_config()

    def _load_config_json(self):
        """读取config.json，增量覆盖，不删除默认key"""
        config_path = "./config.json"
        if not os.path.exists(config_path):
            PrintLog(f"配置文件不存在 {config_path}，使用全部默认配置")
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            # 增量更新：json里面有的key才覆盖，默认字典保留原有key
            self.m_configs.update(json_data)
            PrintLog(f"成功加载外部配置文件: {config_path}")
        except json.JSONDecodeError:
            PrintLog(f"【警告】config.json 解析失败，沿用默认配置")
        except Exception as e:
            PrintLog(f"【警告】读取配置异常: {str(e)}，沿用默认配置")

    def _print_all_config(self):
        PrintLog("===== 加载完成，全部配置项 =====")
        for k, v in self.m_configs.items():
            PrintLog(f"{k} = {v}")
        PrintLog("===============================")

    def GetValue(self, key: str, default=None):
        """获取配置值，可选兜底默认值"""
        return self.m_configs.get(key, default)

    def SetValue(self, key: str, value):
        """运行时修改配置"""
        self.m_configs[key] = value

    def GetDataDir(self):
        return self.m_configs["data_dir"]

    def GetLLMUrl(self):
        return self.m_configs["llm_url"]        

    def GetLLMModel(self):
        return self.m_configs["llm_model_name"]                

    def GetHeavyLLMUrl(self):
        return self.m_configs["heavy_llm_url"]        

    def GetHeavyLLMModel(self):
        return self.m_configs["heavy_llm_model_name"]     


    def GetDeepSeekLLMUrl(self):
        return self.m_configs["deepseek_llm_url"]        

    def GetDeepSeekLLMModel(self):
        return self.m_configs["deepseek_llm_model_name"]     

    def GetDeepSeekKey(self):
        return self.m_configs["deepseek_key"]     

    def GetReportBaseDir(self, stock_code: str) -> str:
        """
        获取某只股票财报根目录: {data_dir}/company/{stock_code}/reports
        """
        base_data = self.GetDataDir()
        return os.path.join(base_data, "company", stock_code, "reports")

    def GetDatasinkingKey(self):
        return self.m_configs["datasinking_key"]     
