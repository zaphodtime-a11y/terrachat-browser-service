# TerraChat Browser Service

Servicio centralizado de navegador para todos los agentes de TerraChat AI.

## ¿Por qué existe este servicio?

Los sandboxes de E2B donde corren los agentes tienen 512MB de RAM. Chromium requiere ~300MB para lanzarse. El agente Python ya usa ~200MB. La suma supera la RAM disponible.

**Solución:** Un sandbox dedicado con 2GB+ RAM donde Playwright/Chromium corre permanentemente. Los agentes hacen llamadas HTTP a este servicio en lugar de lanzar su propio browser.

## Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/healthz` | Health check (sin auth) |
| GET | `/status` | Estado del servicio |
| POST | `/browse` | Navegar URL: devuelve texto + screenshot |
| POST | `/screenshot` | Solo screenshot (más rápido) |
| POST | `/extract` | Solo texto (sin screenshot) |
| POST | `/restart-browser` | Reiniciar Chromium si se cuelga |

## Autenticación

Todas las peticiones (excepto `/healthz`) requieren el header:
```
X-Api-Key: <BROWSER_SERVICE_API_KEY>
```

## Ejemplo de uso (desde un agente)

```python
import requests

BROWSER_URL = "https://your-browser-service.up.railway.app"
API_KEY = "terrachat-browser-2026"

# Navegar y obtener texto + screenshot
resp = requests.post(
    f"{BROWSER_URL}/browse",
    json={"url": "https://example.com", "action": "read"},
    headers={"X-Api-Key": API_KEY},
    timeout=30
)
data = resp.json()
text = data["text"]
screenshot_b64 = data["screenshot"]  # base64 PNG
```

## Deploy en Railway

1. Crear nuevo proyecto en Railway
2. Conectar este repositorio (o subir el código)
3. Railway detecta el Dockerfile automáticamente
4. Configurar variable de entorno: `BROWSER_SERVICE_API_KEY=<tu-clave-secreta>`
5. El servicio necesita **al menos 2GB de RAM** — configurar en Railway > Settings > Resources

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `BROWSER_SERVICE_API_KEY` | `terrachat-browser-2026` | Clave de autenticación |
| `PORT` | `8080` | Puerto del servidor |
