import os
import sys
import subprocess
import sqlite3
import psutil
from datetime import datetime

# При запуске сервера убиваем старых агентов
def kill_old_agents():
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            if proc.info['name'] == 'python.exe':
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if 'agent.py' in cmdline:
                    proc.kill()
                    print(f"Убит старый агент PID: {proc.info['pid']}")
    except Exception as e:
        print(f"Ошибка при убийстве агентов: {e}")

kill_old_agents()

os.environ['TZ'] = 'Europe/Moscow'
try:
    import time
    time.tzset()
except:
    pass

# путь импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# переход в корень проекта
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# создание папок
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# создание CSS (если его нет)
css_file = "static/style.css"
if not os.path.exists(css_file):
    with open(css_file, "w", encoding="utf-8") as f:
        f.write("""body { background: #f5f5f5; }""")

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Depends
import secrets
import asyncio
import json
from typing import Dict
from pydantic import BaseModel
from fastapi.responses import Response

# импорт БД
from backend.database_simple import (
    init_db, add_agent, get_agent_by_token, update_heartbeat,
    add_task, update_task_result, get_all_agents, get_recent_tasks,
    get_task, start_task, get_agent, increment_retry
)

# создание приложение
app = FastAPI(title="Infra Diagnostic System")
security = HTTPBasic()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# очередь задач
task_queues: Dict[int, asyncio.Queue] = {}

# pydantic модели
class AgentRegister(BaseModel):
    name: str
    registration_token: str

class Heartbeat(BaseModel):
    token: str

class TaskCreate(BaseModel):
    agent_id: int
    type: str
    params: dict

class TaskResult(BaseModel):
    result: dict
    logs: str = ""
    exit_code: int = 0

def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = "admin"
    correct_password = "admin123"
    if credentials.username != correct_username or credentials.password != correct_password:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

# WEB UI
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, auth=Depends(verify_auth)):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, auth=Depends(verify_auth)):
    agents = get_all_agents()
    return templates.TemplateResponse("agents.html", {"request": request, "agents": agents})

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, auth=Depends(verify_auth)):
    tasks = get_recent_tasks(50)
    return templates.TemplateResponse("tasks.html", {"request": request, "tasks": tasks})

# статистика для главной страницы
@app.get("/api/stats")
async def get_stats():
    agents = get_all_agents()
    tasks = get_recent_tasks(1000)

    online_agents = len([a for a in agents if a["status"] == "online"])
    total_tasks = len(tasks)
    completed_tasks = len([t for t in tasks if t["status"] == "done"])
    success_rate = round((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0

    return {
        "total_agents": len(agents),
        "online_agents": online_agents,
        "total_tasks": total_tasks,
        "success_rate": success_rate
    }

# получение данных для визуализации связей
@app.get("/api/connections")
async def get_connections():
    agents = get_all_agents()
    tasks = get_recent_tasks(500)

    nodes = []
    edges = []

    for agent in agents:
        nodes.append({
            "id": f"agent_{agent['id']}",
            "label": agent['name'],
            "group": agent.get('group_name', 'default'),
            "type": "agent",
            "status": agent['status']
        })

    for task in tasks:
        if task['status'] != 'done':
            continue

        result = task.get('result')
        if not result:
            continue

        # Парсим результат
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                continue

        if task['type'] == 'port_check':
            host = result.get('host')
            port = result.get('port')
            if host and port and result.get('reachable'):
                node_id = f"host_{host}_{port}"
                # Добавляем узел хоста если его нет
                if not any(n['id'] == node_id for n in nodes):
                    nodes.append({
                        "id": node_id,
                        "label": f"{host}:{port}",
                        "group": "service",
                        "type": "service"
                    })
                edges.append({
                    "from": f"agent_{task['agent_id']}",
                    "to": node_id,
                    "label": f"доступен"
                })

        elif task['type'] == 'service_discovery':
            if result.get('services'):
                for service_name, port in result.get('services', {}).items():
                    node_id = f"service_{service_name}"
                    if not any(n['id'] == node_id for n in nodes):
                        nodes.append({
                            "id": node_id,
                            "label": service_name,
                            "group": "service",
                            "type": "service"
                        })
                    edges.append({
                        "from": f"agent_{task['agent_id']}",
                        "to": node_id,
                        "label": f"порт {port}"
                    })

    return {"nodes": nodes, "edges": edges}

@app.get("/connections", response_class=HTMLResponse)
async def connections_page(request: Request, auth=Depends(verify_auth)):
    return templates.TemplateResponse("connections.html", {"request": request})

# сравнить результат задачи с предыдущим запуском того же типа на том же агенте
@app.get("/api/tasks/{task_id}/diff")
async def get_task_diff(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)

    import sqlite3
    conn = sqlite3.connect("infra_diagnostic.db")
    conn.row_factory = sqlite3.Row
    prev = conn.execute(
        "SELECT * FROM tasks WHERE agent_id = ? AND type = ? AND id < ? AND status = 'done' ORDER BY id DESC LIMIT 1",
        (task["agent_id"], task["type"], task_id)
    ).fetchone()
    conn.close()

    if not prev:
        return {"diff": "Нет предыдущих запусков", "has_prev": False}

    prev = dict(prev)

    # сравнение
    current_result = task.get("result", {})
    prev_result = prev.get("result", {})

    if isinstance(current_result, str):
        try:
            current_result = json.loads(current_result)
        except:
            pass
    if isinstance(prev_result, str):
        try:
            prev_result = json.loads(prev_result)
        except:
            pass

    diff = []
    if isinstance(current_result, dict) and isinstance(prev_result, dict):
        all_keys = set(current_result.keys()) | set(prev_result.keys())
        for key in all_keys:
            old_val = prev_result.get(key)
            new_val = current_result.get(key)
            if old_val != new_val:
                diff.append({
                    "field": key,
                    "old": old_val,
                    "new": new_val,
                    "changed": True
                })

    return {
        "has_prev": True,
        "prev_task_id": prev["id"],
        "prev_created_at": prev["created_at"],
        "diff": diff,
        "current_result": current_result,
        "prev_result": prev_result
    }

# API для агентов
@app.post("/api/agents/register")
async def register_agent(agent_data: AgentRegister):
    if agent_data.registration_token != "secret123":
        raise HTTPException(status_code=401, detail="Invalid registration token")

    token = secrets.token_urlsafe(32)
    agent_id = add_agent(agent_data.name, token)
    task_queues[agent_id] = asyncio.Queue()
    return {"agent_id": agent_id, "token": token}

@app.post("/api/agents/{agent_id}/run_template")
async def run_template(agent_id: int, template_name: str):
    from backend.templates import DIAGNOSTIC_TEMPLATES

    if template_name not in DIAGNOSTIC_TEMPLATES:
        raise HTTPException(status_code=404, detail="Template not found")

    template = DIAGNOSTIC_TEMPLATES[template_name]
    task_ids = []

    for check in template["checks"]:
        task_id = add_task(agent_id, check["type"], check["params"])
        if agent_id in task_queues:
            await task_queues[agent_id].put({"task_id": task_id})
        task_ids.append(task_id)

    return {"template": template_name, "task_ids": task_ids, "count": len(task_ids)}

@app.post("/api/agents/heartbeat")
async def heartbeat(heartbeat_data: Heartbeat):
    agent = get_agent_by_token(heartbeat_data.token)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    update_heartbeat(agent["id"])
    return {"status": "ok"}

@app.post("/api/agents/{agent_id}/tasks/claim")
async def claim_task(agent_id: int, token: str):
    agent = get_agent_by_token(token)
    if not agent or agent["id"] != agent_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    if agent_id not in task_queues:
        task_queues[agent_id] = asyncio.Queue()

    try:
        task_data = await asyncio.wait_for(task_queues[agent_id].get(), timeout=30.0)
    except asyncio.TimeoutError:
        return {"task": None}

    task = get_task(task_data["task_id"])
    if task:
        start_task(task["id"])
        params = task["params"]
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except:
                params = {}
        return {"task": task["id"], "type": task["type"], "params": params}
    return {"task": None}

@app.post("/api/agents/{agent_id}/start")
async def start_agent(agent_id: int):
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    project_path = r"C:\Python Projects\UMIRHack 2026"
    command = f'cd /d {project_path}\\agent && python agent.py --start --agent-id {agent_id} --agent-token {agent["token"]} --server http://localhost:8000'
    subprocess.Popen(command, shell=True, creationflags=0x08000000)

    return {"status": "started", "message": "Агент запущен в фоне"}

@app.post("/api/agents/{agent_id}/stop")
async def stop_agent(agent_id: int):
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # обновить статус в БД на offline
    update_heartbeat(agent_id)

    with sqlite3.connect("infra_diagnostic.db") as conn:
        conn.execute("UPDATE agents SET status = 'offline' WHERE id = ?", (agent_id,))
        conn.commit()

    return {"status": "stopped", "message": "Агент остановлен"}

@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: int):
    import sqlite3

    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    conn = sqlite3.connect("infra_diagnostic.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM tasks WHERE agent_id = ?", (agent_id,))
    tasks_deleted = cursor.rowcount

    cursor.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    conn.commit()
    conn.close()

    if agent_id in task_queues:
        del task_queues[agent_id]

    return {"status": "deleted", "tasks_deleted": tasks_deleted}

@app.get("/api/agents/{agent_id}/start-command")
async def get_start_command(agent_id: int):
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    command = f'python agent/agent.py --start --agent-id {agent_id} --agent-token {agent["token"]} --server http://localhost:8000'
    return {"command": command}

@app.get("/api/agents/groups")
async def get_agent_groups():
    agents = get_all_agents()
    groups = {}
    for agent in agents:
        group = agent.get('group_name', 'default')
        if group not in groups:
            groups[group] = []
        groups[group].append(agent)
    return groups

@app.post("/api/agents/{agent_id}/group")
async def set_agent_group(agent_id: int, group_name: str):
    import sqlite3
    conn = sqlite3.connect("infra_diagnostic.db")
    conn.execute("UPDATE agents SET group_name = ? WHERE id = ?", (group_name, agent_id))
    conn.commit()
    conn.close()
    return {"status": "updated", "group": group_name}

@app.get("/api/agents")
async def get_agents():
    return get_all_agents()

@app.get("/api/agents/rating")
async def get_agents_rating():
    agents = get_all_agents()
    tasks = get_recent_tasks(1000)

    rating = []
    for agent in agents:
        agent_tasks = [t for t in tasks if t["agent_id"] == agent["id"]]
        total = len(agent_tasks)
        if total == 0:
            continue

        success = len([t for t in agent_tasks if t["status"] == "done"])
        success_rate = round(success / total * 100)

        durations = []
        for t in agent_tasks:
            if t.get("started_at") and t.get("finished_at"):
                start = datetime.fromisoformat(str(t["started_at"]).replace('Z', '+00:00'))
                finish = datetime.fromisoformat(str(t["finished_at"]).replace('Z', '+00:00'))
                durations.append((finish - start).total_seconds())

        avg_time = round(sum(durations) / len(durations), 1) if durations else 0

        rating.append({
            "id": agent["id"],
            "name": agent["name"],
            "group": agent.get("group_name", "default"),
            "success_rate": success_rate,
            "avg_time": avg_time,
            "total_tasks": total
        })

    rating.sort(key=lambda x: x["success_rate"], reverse=True)
    return rating


# API для UI
@app.post("/api/tasks")
async def create_task(task_data: TaskCreate):
    task_id = add_task(task_data.agent_id, task_data.type, task_data.params)
    if task_data.agent_id in task_queues:
        await task_queues[task_data.agent_id].put({"task_id": task_id})
    return {"task_id": task_id}

@app.get("/api/tasks")
async def get_tasks_list(limit: int = 100):
    tasks = get_recent_tasks(limit)
    return tasks

# задание со звездочкой *
@app.get("/api/tasks/export")
async def export_tasks(format: str = "json"):
    import csv
    from io import StringIO
    from fastapi.responses import Response
    from datetime import datetime

    tasks = get_recent_tasks(1000)

    if format == "json":
        return tasks

    elif format == "csv":
        output = StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)

        writer.writerow([
            "ID",
            "Агент",
            "Тип",
            "Статус",
            "Создана",
            "Начата",
            "Завершена",
            "Длительность (сек)",
            "Результат"
        ])

        agents = get_all_agents()
        agent_names = {a["id"]: a["name"] for a in agents}

        def parse_time(time_str):
            if not time_str:
                return None
            try:
                time_str = str(time_str).replace('T', ' ').split('.')[0][:19]
                return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            except:
                return None

        def format_text(text):
            if not text:
                return ""

            if isinstance(text, str):
                try:
                    text = json.loads(text)
                except:
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    return "; ".join(lines)[:300]

            if isinstance(text, dict):
                if "result" in text:
                    result_val = text["result"]
                    if isinstance(result_val, str):
                        lines = [line.strip() for line in result_val.split('\n') if line.strip()]
                        return "; ".join(lines)[:300]
                    return str(result_val)[:300]

                if "services" in text:
                    services = text["services"]
                    if services:
                        return "Обнаружены: " + "; ".join([f"{name}:{port}" for name, port in services.items()])
                    return "Сервисы не обнаружены"

                parts = []
                for key, value in text.items():
                    if isinstance(value, list):
                        parts.append(f"{key}: {', '.join(str(v) for v in value[:5])}")
                    elif isinstance(value, dict):
                        continue
                    else:
                        parts.append(f"{key}: {value}")
                return "; ".join(parts)[:300]

            return str(text).replace('\n', '; ')[:300]

        for task in tasks:
            duration = ""
            start_time = parse_time(task.get("started_at"))
            finish_time = parse_time(task.get("finished_at"))

            if start_time and finish_time:
                seconds = (finish_time - start_time).total_seconds()
                duration = f"{seconds:.1f}"

            writer.writerow([
                task["id"],
                agent_names.get(task["agent_id"], "unknown"),
                task["type"],
                task["status"],
                str(task.get("created_at", ""))[:19],
                str(task.get("started_at", ""))[:19],
                str(task.get("finished_at", ""))[:19],
                duration,
                format_text(task.get("result"))
            ])

        return Response(
            content=output.getvalue().encode('utf-8-sig'),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=tasks_export.csv"}
        )

    raise HTTPException(status_code=400, detail="Invalid format")

@app.post("/api/tasks/{task_id}/result")
async def submit_result(task_id: int, result: TaskResult):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if result.exit_code != 0 and task.get("retry_count", 0) < task.get("max_retries", 3):
        increment_retry(task_id)
        task_queues[task["agent_id"]].put({"task_id": task_id})
        return {"status": "retrying", "retry_count": task.get("retry_count", 0) + 1}

    update_task_result(task_id, result.result, result.logs, result.exit_code)
    return {"status": "ok"}

@app.get("/api/tasks/{task_id}")
async def get_task_info(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    if isinstance(task.get("params"), str):
        try:
            task["params"] = json.loads(task["params"])
        except:
            pass
    if isinstance(task.get("result"), str):
        try:
            task["result"] = json.loads(task["result"])
        except:
            pass
    return task

@app.get("/api/tasks/{task_id}/status")
async def get_task_status(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": task["status"], "finished_at": task.get("finished_at")}

@app.delete("/api/tasks/clear")
async def clear_tasks(period: str = "all"):
    import sqlite3
    from datetime import datetime, timedelta

    conn = sqlite3.connect("infra_diagnostic.db")
    cursor = conn.cursor()

    now = datetime.now()

    if period == "hour":
        cutoff = now - timedelta(hours=1)
        cursor.execute("DELETE FROM tasks WHERE created_at < ?", (cutoff,))
    elif period == "day":
        cutoff = now - timedelta(days=1)
        cursor.execute("DELETE FROM tasks WHERE created_at < ?", (cutoff,))
    elif period == "week":
        cutoff = now - timedelta(weeks=1)
        cursor.execute("DELETE FROM tasks WHERE created_at < ?", (cutoff,))
    elif period == "month":
        cutoff = now - timedelta(days=30)
        cursor.execute("DELETE FROM tasks WHERE created_at < ?", (cutoff,))
    elif period == "all":
        cursor.execute("DELETE FROM tasks")
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid period")

    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    return {"status": "cleared", "period": period, "deleted": deleted}

# ========================================================================

# WebSocket
active_connections: list = []

init_db()

print("Сервер работает")
print("http://localhost:8000")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)