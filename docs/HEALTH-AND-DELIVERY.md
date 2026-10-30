# Salud funcional y entrega de avisos

El healthcheck comprueba por separado el heartbeat y cada tienda configurada.
Devuelve JSON con `process`, `shops` y `healthy`, sin credenciales ni datos privados.
Una tienda suspendida, sin lecturas válidas recientes, con estado inválido o con
una entrega fallida/incierta deja el contenedor `unhealthy`. Esto no reinicia el
contenedor automáticamente: conectar ese estado a la supervisión externa.

Las pausas programadas por límites HTTP y fallos temporales se respetan con 90
segundos de margen. Una pausa nunca oculta más de una hora sin datos. El primer
arranque concede 15 minutos para una lectura válida. Una tienda suspendida sigue
requiriendo revisión manual; no se levantan bloqueos ni se modifica la política
de consultas a las tiendas.

Cada aviso conserva su evento original y su estado por canal. Telegram desactivado
queda como `disabled`, nunca como enviado. Los rechazos temporales explícitos
(Telegram 429 y SMTP DATA 4xx) permiten hasta tres intentos dentro de 15 minutos;
se respeta Retry-After de Telegram. Los intentos de Telegram y correo son independientes.
Las entregas `attempting` encontradas tras un reinicio pasan a `uncertain` y no se
repiten. Un timeout o fallo genérico no demuestra que no se haya entregado el
mensaje: queda visible y requiere revisión, sin reenvío automático.

Los reintentos se procesan en los ciclos, incluso con una tienda en pausa, sin
volver a consultar su web para efectuar el envío. No se recrean eventos antiguos
fallidos ni se alerta por una primera observación disponible: se mantiene el
criterio agotado → disponible. La generación de stock no autoriza compras reales.

## Despliegue y comprobación

1. Respaldar estado y configuración sin subir secretos a GitHub.
2. Pasar la suite completa de pruebas en Linux y revisar el cambio.
3. Reconstruir y sustituir solo el monitor durante una intervención controlada.
4. Consultar el healthcheck y comprobar últimas lecturas y entregas por tienda.
5. Verificar en Kuma que `unhealthy` produce un aviso visible; una comprobación
   únicamente del proceso o del host no detecta el fallo funcional.

La imagen ejecuta el monitor con UID/GID 1000. La CI prueba escritura de los tres
directorios de runtime, bloqueo de instancia y Chromium sin red ni secretos.
Antes de actualizar una instalación existente, respaldar sus datos y comprobar
que únicamente los directorios montados de data, screenshots y logs permiten
lectura/escritura a UID1000. No cambiar recursivamente el propietario del proyecto
completo ni dar permisos globales. El código de la imagen permanece de solo lectura
para el usuario del monitor. Un estado stale durante Retry-After necesita un aviso,
no un reinicio ni consultas forzadas a la tienda.
