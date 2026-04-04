from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

class AgentRegister(BaseModel):
    name: str
    registration_token: str

class Heartbeat(BaseModel):
    token: str

class TaskCreate(BaseModel):
    agent_id: int
    type: str  # host_info, port_check, command, service_discovery
    params: Dict[str, Any]

class TaskResult(BaseModel):
    result: Dict[str, Any]
    logs: Optional[str] = ""
    exit_code: int = 0