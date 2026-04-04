# Шаблоны диагностических проверок
DIAGNOSTIC_TEMPLATES = {
    "base_diagnostic": {
        "name": "Базовая диагностика узла",
        "description": "Сбор основной информации о хосте и сети",
        "checks": [
            {"type": "host_info", "params": {}},
            {"type": "network_info", "params": {}},
            {"type": "open_ports", "params": {}}
        ]
    },
    "network_context": {
        "name": "Сбор сетевого контекста",
        "description": "Проверка доступности внутренних сервисов",
        "checks": [
            {"type": "network_info", "params": {}},
            {"type": "port_check", "params": {"host": "google.com", "port": 80}},
            {"type": "port_check", "params": {"host": "localhost", "port": 3306}},
            {"type": "port_check", "params": {"host": "localhost", "port": 5432}}
        ]
    },
    "service_check": {
        "name": "Проверка внутренних сервисов",
        "description": "Проверка доступности критических сервисов",
        "checks": [
            {"type": "service_discovery", "params": {}},
            {"type": "command", "params": {"cmd": "systeminfo"}},
            {"type": "command", "params": {"cmd": "tasklist"}}
        ]
    }
}