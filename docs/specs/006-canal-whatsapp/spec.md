# Spec 006 — Canal WhatsApp con YCloud

- **Status:** Approved
- **Date:** 2026-10-20
- **Scope:** virtual-assistant — nuevo endpoint de eventos de WhatsApp (YCloud), coordinador de WhatsApp y su
  integración con el asistente existente

## Por qué

Los mensajes que llegan al WhatsApp personal de Marco (+56 9 5195 9027) los responde él a mano, a cualquier hora,
aunque muchas consultas son repetitivas y ya están cubiertas por el conocimiento del asistente (perfil profesional,
experiencia, proyectos y disponibilidad). El número ya está conectado a la API oficial de WhatsApp mediante YCloud en
modo coexistencia: la app del celular sigue funcionando y los mensajes nuevos llegan también por webhook.

Se quiere que el asistente responda esos mensajes en nombre de Marco, de forma natural y sin cambiar la experiencia del
contacto, y que Marco pueda tomar cualquier conversación respondiendo desde su celular, momento en que el asistente
deja de intervenir en ella durante un tiempo.

## Decisiones

| Tema | Decisión |
|---|---|
| Proveedor | YCloud en coexistencia; el contacto escribe al número de siempre. |
| A quién atiende | Todos los chats 1 a 1, contactos guardados y desconocidos. Los grupos no llegan por la API. |
| Agente que responde | Nuevo coordinador «Agente WhatsApp» con prompt propio en primera persona, configurable en BD, que delega en las áreas de la audiencia `externo`. |
| Identificación como IA | Ninguna en esta fase: el sistema no agrega presentaciones ni marcas; el tono lo define el prompt. Se reevaluará después. |
| Toma de la conversación | Un mensaje enviado por Marco desde la app pausa al asistente en esa conversación durante una ventana configurable que se renueva con cada mensaje suyo (12 h por defecto). |
| Tipos de mensaje | Texto, imágenes, documentos y notas de voz; los archivos se leen como en Google Chat y los audios se transcriben. |
| Mensajes en ráfaga | Se agrupan: el asistente espera un tiempo configurable sin mensajes nuevos del contacto (8 s por defecto) y responde una sola vez. |
| Fallos | Silencio hacia el contacto y registro en el log; Marco responde desde su celular. |
| Mensajes durante la pausa y mensajes de Marco | Se registran en el historial de la conversación para que el asistente tenga el contexto al retomar (decidido por defecto). |
| Mensajes no soportados | Videos, stickers, ubicaciones, contactos y reacciones no generan respuesta por sí solos (decidido por defecto). |
| Interruptor general | Property que habilita el canal; si falta, el canal queda deshabilitado (decidido por defecto). |
| Lectura | Los mensajes respondidos se marcan como leídos al enviar la respuesta (decidido por defecto). |

## Requisitos funcionales

"El sistema" es la API del asistente en su endpoint de eventos de WhatsApp (`POST /api/v1/whatsapp/events`);
"contacto" es quien escribe al número; "Marco" es el dueño del número; "lote" es el conjunto de mensajes de un contacto
agrupados en una sola respuesta; "ventana de pausa" y "espera de agrupación" son los parámetros configurables (12 h y
8 s por defecto); "tiempo máximo" es el límite total para responder un lote.

### Recepción del webhook

- **RF-01** — Cuando llegue un evento con la firma `YCloud-Signature` válida para el secreto configurado, el sistema
  debe responder `200` sin esperar a que el asistente genere la respuesta.
- **RF-02** — Si el evento no trae la cabecera `YCloud-Signature`, la firma no coincide con el cuerpo recibido o su
  marca de tiempo difiere en más de 5 minutos de la hora del servidor, entonces el sistema debe responder `401` sin
  procesar el evento.
- **RF-03** — Si el secreto del webhook no está configurado, entonces el sistema debe responder `503`, registrar el
  error en el log y no procesar el evento.
- **RF-04** — Cuando llegue un evento con un `id` ya recibido, el sistema debe responder `200` sin volver a procesarlo.
- **RF-05** — Cuando llegue un evento de un tipo distinto de mensaje entrante, mensaje enviado desde la app o
  actualización de estado, el sistema debe responder `200` sin procesarlo.
- **RF-06** — Mientras el canal esté deshabilitado o su property no exista, el sistema debe responder `200` a los
  eventos válidos sin responder al contacto ni pausar conversaciones.

### Respuesta del asistente

- **RF-07** — Cuando llegue un mensaje entrante de un contacto en una conversación no pausada, el sistema debe esperar
  la espera de agrupación desde el último mensaje de ese contacto antes de procesar el lote.
- **RF-08** — Cuando llegue un nuevo mensaje del contacto durante la espera de agrupación, el sistema debe reiniciar la
  espera e incluir el mensaje en el mismo lote.
- **RF-09** — Cuando venza la espera de agrupación, el sistema debe pedir al coordinador «Agente WhatsApp» una única
  respuesta con todos los mensajes del lote en el orden en que llegaron.
- **RF-10** — El sistema debe mantener un historial por número de contacto, separado del chat web y de Google Chat.
- **RF-11** — Mientras se procesa un lote de un contacto, el sistema debe agrupar los mensajes nuevos de ese contacto
  en un lote siguiente que se procesa después de enviar la respuesta en curso.
- **RF-12** — Cuando el asistente obtenga la respuesta, el sistema debe enviarla al contacto como mensaje de texto de
  WhatsApp por la API de YCloud.
- **RF-13** — Si la respuesta supera el largo máximo de un mensaje de texto de WhatsApp, entonces el sistema debe
  enviarla en varios mensajes consecutivos, en orden y sin cortar palabras.
- **RF-14** — Cuando el sistema envíe la respuesta, debe marcar como leídos los mensajes del lote.
- **RF-15** — El sistema no debe agregar al texto de la respuesta presentaciones, firmas ni marcas que identifiquen al
  asistente.
- **RF-16** — El sistema debe ofrecer al coordinador de WhatsApp solo herramientas de consulta, sin acciones que
  modifiquen datos o sistemas.

### Tipos de mensaje

- **RF-17** — Cuando un mensaje del lote traiga una imagen o un documento, el sistema debe descargarlo desde YCloud,
  convertirlo a texto con la lectura de adjuntos existente y sumarlo al lote junto a su leyenda, si la tiene.
- **RF-18** — Cuando un mensaje del lote sea una nota de voz o un audio, el sistema debe transcribirlo y sumar la
  transcripción al lote.
- **RF-19** — Si un archivo o audio supera 20 MB, no se puede descargar o no se puede leer, entonces el sistema debe
  sumar al lote una línea que indique que no se pudo leer y responder igual el resto del lote.
- **RF-20** — Cuando un mensaje sea un video, sticker, ubicación, tarjeta de contacto o reacción, el sistema debe
  registrarlo en el historial como una línea descriptiva sin que por sí solo genere una respuesta.
- **RF-21** — Si un lote solo contiene mensajes del tipo de RF-20, entonces el sistema no debe enviar respuesta.

### Toma de la conversación por Marco

- **RF-22** — Cuando llegue un mensaje enviado por Marco desde la app de WhatsApp Business, el sistema debe pausar al
  asistente en esa conversación hasta que venza la ventana de pausa contada desde ese mensaje.
- **RF-23** — Cuando llegue otro mensaje de Marco en una conversación pausada, el sistema debe renovar la ventana de
  pausa desde ese mensaje.
- **RF-24** — Mientras una conversación esté pausada, el sistema no debe responder los mensajes del contacto.
- **RF-25** — Cuando llegue un mensaje enviado por Marco mientras un lote de esa conversación espera o se procesa, el
  sistema debe descartar ese lote sin enviar su respuesta.
- **RF-26** — El sistema debe registrar en el historial de la conversación los mensajes de Marco y los del contacto
  recibidos durante la pausa, sin generar respuestas por ellos.
- **RF-27** — Cuando venza la ventana de pausa, el sistema debe responder solo los mensajes del contacto recibidos
  después del vencimiento.

### Configuración

- **RF-28** — El sistema debe leer desde la tabla `property`, en cada evento, la habilitación del canal, la API key y
  el secreto del webhook de YCloud, la ventana de pausa, la espera de agrupación y el tiempo máximo.
- **RF-29** — Si la ventana de pausa no está configurada, entonces el sistema debe usar 12 horas.
- **RF-30** — Si la espera de agrupación no está configurada, entonces el sistema debe usar 8 segundos.
- **RF-31** — Si el tiempo máximo no está configurado, entonces el sistema debe usar 120 segundos.

### Fallos

- **RF-32** — Si el coordinador «Agente WhatsApp», sus sub-agentes, el LLM, la base de datos o la API key no están
  disponibles al procesar un lote, entonces el sistema no debe enviar nada al contacto y debe registrar el error en el
  log sin el contenido de los mensajes.
- **RF-33** — Si el procesamiento de un lote supera el tiempo máximo, entonces el sistema debe descartarlo sin enviar
  nada al contacto y registrar la demora en el log.
- **RF-34** — Si YCloud rechaza el envío de la respuesta, entonces el sistema debe registrar el error en el log sin
  reintentar.
- **RF-35** — Cuando llegue una actualización de estado que indique que un mensaje del asistente no se entregó, el
  sistema debe registrarlo en el log con el motivo informado por YCloud.

## Fuera de alcance

- Identificar al asistente como IA (presentación, prefijo o firma): descartado por ahora; se evaluará en otra spec.
- Respuesta fija de error al contacto: descartada; ante fallos se guarda silencio.
- Responder mensaje por mensaje sin agrupar: descartado.
- Pausa indefinida o hasta fin del día: descartadas; se usa la ventana configurable.
- Audiencia o áreas propias de WhatsApp: descartado; se reutilizan las áreas `externo`.
- Reutilizar el «Agente Externo» tal cual: descartado porque habla de Marco en tercera persona.
- Avisos a Google Chat, escalar consultas desconocidas y crear FAQs desde Google Chat: fases 2 a 4.
- Leer videos, stickers, ubicaciones y tarjetas de contacto.
- Mensajes iniciados por el negocio, plantillas y respuestas fuera de la ventana de 24 h de WhatsApp.
- Indicador de "escribiendo…", reacciones y respuestas citando un mensaje.
- Reactivar manualmente una conversación pausada o listas de contactos ignorados.
- Configurar YCloud o el webhook por API: se hace a mano en el panel de YCloud.
- Corregir la descripción desactualizada de `search_faq` («consulta sobre Autofin»): se registra aparte.

## Restricciones

- **Aprobación de esquema pendiente:** la deduplicación de eventos, el estado de pausa por conversación y el lote en
  espera deben sobrevivir a reinicios y funcionar con varias réplicas; el plan definirá si requieren tablas nuevas en
  `src/models/` y su migración.
- **Datos, no esquema:** el coordinador «Agente WhatsApp» es una fila nueva en `agents` (sin área) y su prompt se carga
  por SQL documentado en el README; las properties nuevas son filas de `property`.
- **Sin dependencias nuevas** previstas: HTTP con `httpx` y la firma con la librería estándar.
- **Secretos:** la API key y el secreto del webhook van en `property` (constitución §6); revisar `SECURITY.md`.
- **Privacidad:** los mensajes personales del número quedan en el historial de la base de datos; el log no debe
  incluir su contenido.
- **Infraestructura:** el endpoint debe ser accesible por HTTPS público para YCloud; el webhook se registra a mano en
  YCloud con los tres eventos.
- Constitución: §2 (capas), §4 (tests sin BD real, sin LLM y sin YCloud), §6 (configuración), §7 (sin trazas al
  cliente), §9 (versión al cerrar la spec).

## Criterios de finalización

- [ ] Cada RF tiene al menos un test en `tests/` que pasa con `uv run pytest`, sin BD real, sin LLM y sin llamadas
      reales a YCloud.
- [ ] `uv run pyright` termina con 0 errores y `uv run alembic check` no reporta diferencias; migraciones aplicadas
      si el plan aprobado agrega tablas.
- [ ] README documenta el endpoint, las properties nuevas con sus valores por defecto, la carga del «Agente WhatsApp»
      y la configuración del webhook en YCloud.
- [ ] Versión minor subida en `pyproject.toml` y `uv sync` ejecutado, en un solo commit con `uv.lock`.
- [ ] Verificación manual: desde otro teléfono, tres mensajes seguidos y una nota de voz reciben una sola respuesta
      en primera persona; Marco responde desde su celular y el siguiente mensaje del contacto no recibe respuesta del
      asistente; con el canal deshabilitado no se responde nada.
