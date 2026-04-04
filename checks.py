import socket
import platform
import subprocess
from typing import Dict, List

ALLOWED_COMMANDS = ["ipconfig", "systeminfo", "tasklist", "netstat -an"]

# базовая инфо о хосте
def get_host_info() -> Dict:
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "os_version": platform.version(),
        "python_version": platform.python_version(),
        "processor": platform.processor(),
        "machine": platform.machine()
    }

# сетевая инфо
def get_network_info() -> Dict:
    hostname = socket.gethostname()

    ip_addresses = []
    try:
        ip_addresses = socket.gethostbyname_ex(hostname)[2]
    except:
        pass

    if not ip_addresses:
        ip_addresses = ['127.0.0.1']

    return {
        "hostname": hostname,
        "ip_addresses": ip_addresses
    }

# проверка доступности порта
def check_port(host: str, port: int, timeout: int = 2) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

# выполнение разрешённой команды
def execute_command(cmd: str) -> Dict:
    if cmd not in ALLOWED_COMMANDS:
        return {"result": f"Команда запрещена"}

    try:
        encoding = 'cp866' if platform.system() == "Windows" else 'utf-8'
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=10, encoding=encoding
        )

        if result.stdout:
            # Чистим строки
            clean_lines = []
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line and not line.startswith('==') and not line.startswith('--') and not set(line).issubset(
                        {'=', '-', ' '}):
                    # Убираем лишние пробелы
                    clean_lines.append(' '.join(line.split()))

            output = "; ".join(clean_lines[:10])
        else:
            output = "Вывод пуст"

        return {"result": output}
    except:
        return {"result": "Ошибка выполнения"}

def get_open_ports() -> List[int]:
    common_ports = [80, 443, 22, 3306, 5432, 6379, 8080, 3389]
    open_ports = []

    for port in common_ports:
        if check_port("127.0.0.1", port, timeout=0.5):
            open_ports.append(port)

    return open_ports

# обнаружение сервисов
def get_service_discovery() -> Dict:
    services = {
        "HTTP": 80,
        "HTTPS": 443,
        "SSH": 22,
        "MySQL": 3306,
        "PostgreSQL": 5432,
        "Redis": 6379,
        "Tomcat": 8080,
        "RDP": 3389
    }

    discovered = []
    for service_name, port in services.items():
        if check_port("127.0.0.1", port, timeout=0.5):
            discovered.append(f"{service_name}:{port}")

    if discovered:
        return {"result": "Обнаружены: " + ", ".join(discovered)}
    else:
        return {"result": "Сервисы не обнаружены"}

# краткая сводка
def generate_summary(task_type: str, result: dict) -> str:
    if task_type == "host_info":
        return f"Хост: {result.get('hostname', 'unknown')}, ОС: {result.get('os', 'unknown')}"

    elif task_type == "network_info":
        ips = result.get('ip_addresses', [])
        return f"IP-адреса: {', '.join(ips) if ips else 'не определены'}"

    elif task_type == "port_check":
        host = result.get('host', 'unknown')
        port = result.get('port', 'unknown')
        status = "доступен" if result.get('reachable') else "недоступен"
        return f"Порт {host}:{port} {status}"

    elif task_type == "open_ports":
        ports = result.get('open_ports', [])
        return f"Открытые порты: {', '.join(map(str, ports)) if ports else 'не найдены'}"

    elif task_type == "service_discovery":
        services = list(result.keys())
        return f"Обнаружены сервисы: {', '.join(services) if services else 'не найдены'}"

    elif task_type == "command":
        cmd = result.get('command', 'unknown')
        if 'error' in result:
            return f"Команда '{cmd}' завершилась с ошибкой"
        return f"Команда '{cmd}' выполнена успешно"

    return "Проверка выполнена"