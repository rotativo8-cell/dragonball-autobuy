# dragonball-autobuy

Monitor Python + Chromium para Ubuntu Server con Docker Compose. **Esta versión no compra:** solo acepta `DRY_RUN=true`. No contiene ninguna acción de confirmar o pagar.

## Qué funciona ahora

El módulo `demo` usa una página local de ejemplo, detecta stock y precio, simula un carrito de **una unidad** y se queda antes de confirmar. También existe el módulo `ninnin` para el producto indicado abajo. La demostración no visita tiendas reales.

Para monitorizar otra tienda real, un desarrollador deberá añadir su módulo y verificar sus pasos. El módulo genérico define el contrato y las reglas; no puede detectar automáticamente el checkout, CAPTCHA o 3D Secure de una tienda desconocida.

## Instalar por primera vez

1. En Ubuntu Server instala **Docker Engine y el plugin Docker Compose** siguiendo la guía oficial: https://docs.docker.com/engine/install/ubuntu/. Comprueba `docker --version` y `docker compose version`.
2. Instala Git si hace falta con `sudo apt update` y `sudo apt install -y git`. Los cambios de esta versión deben haberse subido a la rama `work` del remoto antes de desplegar. Descarga esa rama:
   ```bash
   git clone --branch work https://github.com/rotativo8-cell/dragonball-autobuy.git
   cd dragonball-autobuy
   ```
   Si esta versión ya se ha fusionado en `main`, sustituye `--branch work` por `--branch main`. Para un repositorio privado necesitarás acceso de GitHub en tu servidor; nunca pongas un token en la URL ni en el README.
3. Crea la configuración y abre el editor:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Mantén `DRY_RUN=true` y `SHOP_MODULES=demo` para empezar. Guarda con Ctrl+O, Intro y sal con Ctrl+X. No compartas `.env`.
4. Comprueba y arranca:
   ```bash
   docker compose config --quiet
   docker compose up -d --build
   docker compose logs -f
   ```
   Si Docker requiere permisos, antepón `sudo` a los comandos Docker. Ctrl+C deja de mostrar los logs; el contenedor sigue funcionando.

Los archivos persistentes quedan en **`./data`** (estado y sesión del navegador) y las capturas en **`./screenshots`**, fuera de la imagen Docker. Se conservan al reconstruir o recrear el contenedor. `.env` y esas carpetas no se incluyen en Git; únicamente se versionan sus marcadores vacíos.

La primera ejecución debe mostrar `DRY_RUN completado`, envío 5 y total 25. Sin Telegram configurado, el aviso aparece en logs. En ejecuciones siguientes dice `evento ya registrado`.

## Configuración

Edita `.env` y después ejecuta `docker compose up -d --force-recreate`.

| Variable | Significado |
| --- | --- |
| `DRY_RUN` | Obligatoriamente `true`. `false` detiene el programa. |
| `CHECK_INTERVAL_SECONDS` | 45 por defecto, mínimo 30; segundos entre inicios de revisión, salvo que el ciclo tarde más. |
| `SHOP_MODULES` | Uno o dos nombres de módulos, separados por coma; `demo`, `ninnin` o `demo,ninnin`. |
| `MAX_PRODUCT_PRICE` | Precio máximo del producto: 50.00 por defecto. |
| `MAX_TOTAL_PRICE` | Total máximo con envío e impuestos: 60.00. |
| `MAX_SHIPPING_PRICE` | Envío máximo: 10.00. |
| `MAX_QUANTITY` | Obligatoriamente `1`; otros valores detienen el programa. |
| `TELEGRAM_BOT_TOKEN` | Token secreto del bot. |
| `TELEGRAM_CHAT_ID` | Identificador del chat al que enviar avisos. |
| `ALLOW_NO_TELEGRAM` | `true` solo para pruebas sin Telegram; usa `false` para monitorización. |

Usa punto decimal, sin símbolos de moneda. Todos los límites deben estar en la misma moneda que la tienda; no hay conversión de divisas. En Docker las carpetas son fijadas por Compose.

## Telegram

1. En Telegram abre el bot oficial **@BotFather**, escribe `/newbot` y sigue sus instrucciones. Copia el token únicamente en `.env`.
2. Abre tu nuevo bot y envíale `/start`.
3. Obtén tu chat ID mediante la API oficial `getUpdates` de Telegram, con ayuda si la necesitas: https://core.telegram.org/bots/api#getupdates. Evita compartir el token o publicarlo en capturas.
4. Rellena ambas variables y cambia `ALLOW_NO_TELEGRAM=false`. Reinicia como se indica arriba. La primera detección enviará un aviso. Un error de envío detiene el flujo y guarda una parada crítica.

## Mantener y detener

```bash
docker compose ps                   # Estado y salud
docker compose logs --tail=100      # Últimos mensajes
docker compose stop                # Detener
docker compose up -d                # Volver a iniciar
git pull                           # Descargar cambios del proyecto
docker compose up -d --build        # Reconstruir después de actualizar
```

Guarda una copia segura de `.env`, `data/` y, si lo necesitas, `screenshots/`. `data/browser/` conserva cookies y sesiones: trátalo como información privada. Las capturas también pueden contener datos personales. Borra capturas antiguas manualmente para controlar el espacio.

## Paradas y duplicados

`data/state.json` guarda los eventos antes de notificar o tocar el carrito. En `demo`, un evento no se repite hasta que desaparece y vuelve el stock. Nin-Nin conserva además un bloqueo permanente de intentos, explicado abajo. Después de una caída puede quedar `claimed`: se evita repetir una acción de resultado incierto.

Una situación insegura crea `data/critical.json`, intenta guardar una captura y envía un aviso. El proceso termina con salida normal para que Docker **no reinicie tras el error crítico**. Si Chromium todavía no arrancó, no hay página de la que sacar captura. El healthcheck detecta una parada crítica o más de 270 segundos sin actividad; Docker Compose no reinicia automáticamente por salud `unhealthy`.

Errores inesperados que terminan el proceso con salida distinta de cero tienen hasta tres reinicios automáticos. Los fallos de operaciones de tienda se tratan conservadoramente como críticos.

Para recuperarte: detén el servicio, revisa logs y captura, resuelve la causa, y solo entonces retira `data/critical.json` y arranca. No borres `state.json` sin revisar el carrito y los eventos: podría permitir otra acción. Para repetir **solo la demostración**, con el monitor detenido y sin ninguna tienda real, puedes borrar `data/state.json`.

## Añadir una tienda (para desarrolladores)

Crea `src/shops/nombre.py` con `create_shop()` que devuelva una subclase de `Shop` en `base.py`. `inspect(page)` devuelve disponibilidad y precio mediante `Product`. `prepare_checkout(page, config)` devuelve `Checkout` y se detiene antes de cualquier acción que pueda comprometer un pago. Configura URLs y selectores reales solo tras inspeccionar la tienda; nunca adivines botones.

El módulo debe detectar CAPTCHA, 3D Secure, páginas inesperadas y precios desconocidos antes de cada acción y lanzar `CriticalError`. Verifica carrito vacío, fija cantidad exactamente a uno, comprueba límites antes de cada transición y otra vez al leer el resumen. No implementes un método de compra final. El chequeo común al volver del módulo es una defensa adicional: no reemplaza los chequeos previos internos. Audita que ningún click pueda confirmar una compra, incluso en simulación. El navegador conserva sesión compartida entre módulos; cada tienda utiliza su propia pestaña.

## Pruebas

```bash
python -m unittest discover -s tests -v
```

Necesita Python y las dependencias de `requirements.txt`. Para probar Chromium dentro de Docker sin el monitor ejecutándose:

```bash
docker compose stop
docker compose run --rm monitor python -m src.main --once
```

La demostración y pruebas no verifican ninguna tienda real ni el envío Telegram sin credenciales. Los módulos reales requieren pruebas específicas antes de usarlos.

## Activar Nin-Nin Game: monitor híbrido

Producto fijo: **Dragon Ball: Visual Adventure Premium Set Vol.2 (Limited Edition) [Bandai]**, ID 240042.
URL: https://www.nin-nin-game.com/en/dragon-ball/240042-dragon-ball-visual-adventure-premium-set-vol2-limited-edition-bandai-.html

En `.env` configura:

```dotenv
DRY_RUN=true
SHOP_MODULES=ninnin
CHECK_INTERVAL_SECONDS=45
MAX_QUANTITY=1
NINNIN_CURRENCY=EUR
```

Desactivar: `SHOP_MODULES=demo`. Para ambos: `SHOP_MODULES=demo,ninnin`.
Después ejecuta `docker compose up -d --build --force-recreate`.
En Codex Cloud usa el script de instalación guardado y `docker compose up -d --no-build --force-recreate`, por la limitación DNS del build. En tu Ubuntu doméstico basta el comando normal de Docker.

**Mientras no hay stock no se usa Playwright.** Python hace una petición HTTPS ordinaria con `requests` cada 45 segundos (mínimo configurable 30), sin reintentos automáticos. Se comprueban nombre, ID, oferta JSON-LD, disponibilidad, precio y moneda en el HTML. Una respuesta inesperada, un desafío o un error HTTP se tratan como error crítico; no hay fallback para sortear protecciones. Se conserva la validación TLS, con la CA oficial del entorno cuando está proporcionada. El User-Agent identifica este monitor; no hay spoofing, resolución de CAPTCHA ni bypass.

Cada resultado se guarda en `data/state.json`. Si sigue `OUT_OF_STOCK`, solo se escribe un log, sin mensajes Telegram ni navegador. Al pasar a `IN_STOCK` (también si la primera observación ya tiene stock), el bot guarda fecha/hora ISO UTC, tienda, producto, URL, precio y moneda, y envía Telegram inmediatamente. No repite la misma notificación mientras continúe disponible.

Antes de abrir el navegador se comprueba `MAX_PRODUCT_PRICE`, `MAX_TOTAL_PRICE` y la moneda. Los límites se expresan en `NINNIN_CURRENCY`, sin conversión; los valores de ejemplo 50/60/10 bloquean el precio observado de 94,39 EUR. `MAX_QUANTITY` está bloqueado a una unidad. Si un límite bloquea la acción, sigue la observación HTTP, sin abrir Chromium ni tocar el carrito.

Solo con stock y precios admitidos se abre Chromium con sesión persistente. Al abrir la página se guarda una captura del evento; se verifican otra vez producto, precio y stock. Si hay 403/CAPTCHA/Cloudflare, se guarda `PLAYWRIGHT_BLOCKED`, se notifica, se cierra el navegador y continúa únicamente el monitor HTTP, sin volver a intentar carrito. Si funciona: verifica carrito vacío, añade **exactamente una unidad**, comprueba producto/cantidad y lee subtotal si existe; se detiene **antes de checkout**. Nunca hace login, introduce datos personales, acepta cookies ni pulsa pago/confirmación. Los errores de selector y estados inesperados guardan captura y parada crítica.

`MAX_TOTAL_PRICE` también se aplica al subtotal conocido. Envío y total final todavía no existen antes de checkout; no se inventan ni se avanza para averiguarlos. `MAX_SHIPPING_PRICE` sigue disponible para otros módulos, pero nunca autoriza checkout de Nin-Nin. Al terminar el intento el navegador se cierra; las comprobaciones posteriores siguen usando HTTP.

### Bloqueo de intentos duplicados

Se guardan **antes de abrir el navegador** `purchase_attempted=true` y la fecha del intento. Si una caída, un bloqueo o un resultado incierto deja ese valor, nunca se vuelve a intentar automáticamente, ni después de reiniciar ni si el stock desaparece y regresa.

Al verificar el carrito se guarda `purchase_completed=true` y `outcome=DRY_RUN_CART_VERIFIED`. **Este nombre es un bloqueo de seguridad: no significa que haya habido una compra real.** El modo real está deshabilitado. Ambos indicadores son persistentes y no se borran en una transición a sin stock. Los estados antiguos `claimed` y `completed` se migran conservadoramente a esos bloqueos.

Para reiniciar manualmente: detén el contenedor, revisa el carrito y los eventos, guarda una copia de `data/state.json` y, solo si quieres permitir otro intento, elimina la entrada `ninnin` del JSON. No basta con borrar `critical.json`. Si no sabes editar JSON, pide ayuda; borrar todo el estado también reinicia los eventos de las otras tiendas.

### Telegram y pruebas

Para recibir avisos configura `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` y `ALLOW_NO_TELEGRAM=false`. El envío está preparado y probado con mocks; sin credenciales no se afirma entrega real. `ALLOW_NO_TELEGRAM=true` solo permite pruebas con avisos en logs.

```bash
python -m playwright install chromium
python -m unittest discover -s tests -v
```

En Codex usa `.venv/bin/python` y `PLAYWRIGHT_BROWSERS_PATH=/workspace/dragonball-autobuy/.browsers`. Los tests cubren HTML agotado/disponible, transiciones, límites, errores HTTP, HTML inesperado, Chromium bajo demanda y bloqueos persistentes. Todas las solicitudes de los tests se interceptan; no contactan tiendas ni compran.

Validación real del 6 de octubre de 2026: **HTTP detecta OUT_OF_STOCK, 94,39 EUR y el producto correcto**. No arranca Chromium en ese estado. Playwright continúa sin validarse contra el carrito real de Nin-Nin desde Codex; sus pruebas de carrito usan páginas sintéticas. Podrá utilizarse en tu Ubuntu doméstico, sin quitar ninguna protección y deteniéndose ante estructuras desconocidas.

## Subir esta versión al remoto

Desde el equipo donde se creó el commit, ejecuta `git push -u origin work`. Después podrás usar el comando `git clone --branch work` indicado arriba en Ubuntu. El commit local por sí solo no publica los archivos en GitHub. No subas `.env`, cookies, estado ni capturas. Las credenciales Telegram se introducen únicamente en el `.env` privado de cada servidor.

## Diagnóstico manual de navegador Nin-Nin (Ubuntu doméstico)

Este comando **solo lee la página**: no añade al carrito, no abre checkout, no usa la sesión del monitor, no envía Telegram ni modifica su estado. Rechaza `DRY_RUN=false`; si no está definido, usa `true`. No intenta evadir 403, CAPTCHA ni Cloudflare y mantiene la validación HTTPS.

Después de descargar esta versión, reconstruye la imagen y ejecuta:

```bash
docker compose build
docker compose run --rm --no-deps monitor python -m src.test_ninnin_browser
```

Puede ejecutarse mientras el monitor está activo porque utiliza una sesión aislada. La imagen ya incluye Chromium y sus librerías del sistema; `COPY src ./src` incluye el comando automáticamente. La captura aparece en **`./screenshots/ninnin-browser-test.png`** en Ubuntu (dentro del contenedor: `/app/screenshots/ninnin-browser-test.png`). Cada prueba reemplaza esa captura. Si el navegador no puede arrancar, se indica que la captura no se guardó.

Sin Docker, desde la carpeta del proyecto y con Python instalado:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python -m src.test_ninnin_browser
```

Con el entorno virtual activado (`source .venv/bin/activate`), el comando es `python -m src.test_ninnin_browser`. Este comando nativo no lee `.env`; usa variables exportadas o valores seguros por defecto. Docker Compose sí carga `.env`.

La salida incluye acceso, HTTP, título, producto, precio/moneda, stock, botón de carrito, protección y ruta de captura. Un 403 sin desafío se informa como acceso denegado, sin afirmar que sea CAPTCHA. Devuelve código de salida 0 si todo se detecta y captura correctamente, y 1 ante bloqueo, error de navegación, datos inesperados o fallo de captura. Una salida de error no reinicia ni desbloquea el monitor.
