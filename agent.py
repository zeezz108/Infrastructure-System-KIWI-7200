import asyncio
import httpx
import argparse
import sys
import os
from typing import Optional

# путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checks import (
    get_host_info, get_network_info, check_port,
    execute_command, get_open_ports, get_service_discovery,
    generate_summary
)

class DiagnosticAgent:
    def __init__(self, server_url: str, agent_id: Optional[int] = None, token: Optional[str] = None):
        self.server_url = server_url
        self.agent_id = agent_id
        self.token = token
        self.running = True
        self.semaphore = asyncio.Semaphore(2)

    # регистрация нового агента
    async def register(self, name: str, registration_token: str):

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/api/agents/register",
                json={"name": name, "registration_token": registration_token}
            )
            if response.status_code == 200:
                data = response.json()
                self.agent_id = data["agent_id"]
                self.token = data["token"]
                print(f"Agent ID={self.agent_id}")
                print(f"Token: {self.token}")
                return True
            else:
                print(f"Registration failed: {response.text}")
                return False

    async def heartbeat(self):
        while self.running and self.token:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{self.server_url}/api/agents/heartbeat",
                        json={"token": self.token}
                    )
                await asyncio.sleep(30)
            except Exception as e:
                print(f"Heartbeat error: {e}")
                await asyncio.sleep(10)

    async def execute_task(self, task_type: str, params: dict) -> tuple:
        print(f"🔧 Executing task: {task_type}")

        if task_type == "host_info":
            result = get_host_info()
            logs = "Host info collected"
        elif task_type == "network_info":
            result = get_network_info()
            logs = "Network info collected"
        elif task_type == "port_check":
            host = params.get("host", "localhost")
            port = params.get("port", 80)
            result = {"host": host, "port": port, "reachable": check_port(host, port)}
            logs = f"Checked {host}:{port}"
        elif task_type == "command":
            cmd = params.get("cmd", "")
            result = execute_command(cmd)
            logs = f"Executed: {cmd}"
        elif task_type == "open_ports":
            result = {"open_ports": get_open_ports()}
            logs = "Port scan completed"
        elif task_type == "service_discovery":
            result = get_service_discovery()
            logs = "Service discovery completed"
        else:
            result = {"error": f"Unknown task type: {task_type}"}
            logs = "Task failed"

        summary = generate_summary(task_type, result)
        logs = f"{logs}\n📊 Сводка: {summary}"

        return result, logs

    async def work_loop(self):
        while self.running and self.token:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.server_url}/api/agents/{self.agent_id}/tasks/claim",
                        params={"token": self.token},
                        timeout=35
                    )

                    if response.status_code == 200:
                        task_data = response.json()
                        if task_data.get("task"):
                            task_id = task_data["task"]
                            task_type = task_data["type"]
                            params = task_data["params"]

                            async with self.semaphore:
                                result, logs = await self.execute_task(task_type, params)

                                await client.post(
                                    f"{self.server_url}/api/tasks/{task_id}/result",
                                    json={"result": result, "logs": logs, "exit_code": 0}
                                )
                                print(f"Задача {task_id} выполнена")

                    elif response.status_code == 401:
                        print("Invalid token, stopping...")
                        break
            except Exception as e:
                print(f"Work loop error: {e}")
                await asyncio.sleep(5)

    async def run(self):
        print(f"Старт агента{self.agent_id}")
        await asyncio.gather(
            self.heartbeat(),
            self.work_loop()
        )

    def stop(self):
        self.running = False

async def main():
    parser = argparse.ArgumentParser(description="Infra Diagnostic Agent")
    parser.add_argument("--register", action="store_true", help="Register new agent")
    parser.add_argument("--name", default="my-agent", help="Agent name")
    parser.add_argument("--token", default="secret123", help="Registration token")
    parser.add_argument("--server", default="http://localhost:8000", help="Server URL")
    parser.add_argument("--start", action="store_true", help="Start agent")
    parser.add_argument("--agent-id", type=int, help="Existing agent ID")
    parser.add_argument("--agent-token", help="Existing agent token")
    args = parser.parse_args()

    if args.register:
        agent = DiagnosticAgent(args.server)
        if await agent.register(args.name, args.token):
            print(f"\nSave these credentials for starting the agent:")
            print(f"   --agent-id {agent.agent_id} --agent-token {agent.token}")
        return

    if args.start:
        if not args.agent_id or not args.agent_token:
            print("Please provide --agent-id and --agent-token")
            print("Example: python agent/agent.py --start --agent-id 1 --agent-token <token>")
            return

        agent = DiagnosticAgent(args.server, args.agent_id, args.agent_token)
        try:
            await agent.run()
        except KeyboardInterrupt:
            print("\nAgent stopped")
            agent.stop()

if __name__ == "__main__":
    asyncio.run(main())